# vendor 自 MinerU-Popo (https://github.com/opendatalab/MinerU-Popo, MIT License)
# 原始路径: post_processing/get_json_tree.py
# 本地修改: construct_json_tree 改为纯内存的 build_tree（不再读写文件），
#           输入 block 缺少 contd/level/image 标注字段时自动补 -1。
"""
文档树构建：标注后的扁平 block 列表 → 层级 JSON 树。

build_tree 接收 run_inference 的输出，依次执行：
  1. 补充类型还原（按 source_label 还原 header/footer 等被归并为 text 的类型）
  2. 跨页表格合并（merge_cross_page_tables）
  3. 按标题层级组建文本组件（含 <|txt_contd|>/<|txt_split|> 跨页拼接标记）
  4. 挂载图表等特殊元素（含 caption/footnote 关联）
  5. 挂载页眉页脚等补充元素
"""

import copy
import logging

from .table_merge_utils import merge_cross_page_tables

logger = logging.getLogger(__name__)

special_types = ['table_footnote', 'table', 'chart', 'table_caption', 'image_footnote', 'image', 'image_caption', 'seal']
large_block_types = ['super', 'list', 'ref_block', 'equation_block', 'image_block']
supplement_types = ['page_title', 'page_number', 'page_footnote', 'header', 'aside_text', 'footer']

supplement_source_label_map = {
    'page_title': 'page_title',
    'page_number': 'page_number',
    'page_footnote': 'page_footnote',
    'header': 'header',
    'aside_text': 'aside_text',
    'footer': 'footer',
    'number': 'page_number',
    'footnote': 'page_footnote',
}


def cp_init(cp_type="", title="", metadata="", content="", level=-1, location=None, block_ids=None):
    # Create a component
    cp = {
        'type': cp_type,
        'title': title,
        'metadata': metadata,
        'content': content,
        'level': level,
        'location': [] if not location else location,
        'block_ids': [] if not block_ids else block_ids
    }
    return cp


def build_tree(blocks: list[dict]) -> dict:
    """
    把标注后的扁平 block 列表建成文档树（纯内存，不修改调用方的列表）。

    Args:
        blocks: run_inference 的输出（需含 id/page/bbox/type/content；
            contd/level/image 标注字段缺失时按 -1 处理）。

    Returns:
        文档树 dict：root 节点，children 递归嵌套；文本组件 content 中的
        <|txt_contd|> 表示该段是上一段的跨页延续，<|txt_split|> 表示
        该段在下一页有延续。
    """
    elements = copy.deepcopy(blocks)
    for element in elements:
        element.setdefault('contd', -1)
        element.setdefault('level', -1)
        element.setdefault('image', -1)

    # 补充类型还原
    for element in elements:
        source_label = element.get('source_label')
        if element.get('type') == 'text' and source_label in supplement_source_label_map:
            element['type'] = supplement_source_label_map[source_label]

    elements = merge_cross_page_tables(elements)

    def get_text_components(elements):
        text_components = []
        contd_list = []
        cur_text_title = "Default Title"
        cur_text_cp = cp_init(cp_type="text", title=cur_text_title, level=1)

        for element in elements:
            if element["type"] == "title" and element["level"] < 0:
                element["type"] = "text"

            if element["type"] == "title":
                cur_text_title = element['content']
                if cur_text_cp['title'] != "Default Title" or cur_text_cp['content'] != "":
                    text_components.append(cur_text_cp)
                cur_text_cp = cp_init(cp_type="text", title=cur_text_title, level=element['level'], location=[{'bbox': element['bbox'], 'page': element['page']}], block_ids=[element['id']])

            elif element["type"] not in special_types + large_block_types + supplement_types:
                if element['contd'] >= 0:
                    contd_list.append(element['contd'])
                contd_label = '<|txt_contd|>' if element['id'] in contd_list else '<|txt_split|>'
                element_content = element.get('content') or ''
                cur_text_cp['content'] = cur_text_cp['content'] + contd_label + element_content if cur_text_cp['content'] else element_content
                cur_text_cp['location'].append({'bbox': element['bbox'], 'page': element['page']})
                cur_text_cp['block_ids'].append(element['id'])

        text_components.append(cur_text_cp)

        return text_components

    def construct_by_level(text_components):
        # Initialize the root and stack
        root = cp_init(cp_type="root", level=0)
        root['children'] = []
        stack = [{'node': root, 'level': 0}]

        # Traverse in reading order
        for cp in text_components:

            cp['children'] = []
            level = cp['level'] if cp['level'] > 0 else 100

            # Pop until a higher level (small number)
            while stack[-1]["level"] >= level:
                stack.pop()
            parent = stack[-1]["node"]

            # Link to the parent
            parent["children"].append(cp)

            # Push
            stack.append({"node": cp, "level": level})

        return root

    text_tree = construct_by_level(get_text_components(elements))

    def add_special_elements(text_tree, elements):
        visual_components = []
        for element in elements:
            if element['type'] in ['table', 'chart', 'image', 'seal', 'image_block']:
                locations = element.get('merged_locations', [{'bbox': element['bbox'], 'page': element['page']}])
                block_ids = element.get('merged_block_ids', [element['id']])
                visual_component = cp_init(cp_type=element['type'], content=element['content'], level=element['image'], location=locations, block_ids=block_ids)

                for elem in elements:
                    if elem['image'] == element['id']:
                        if 'caption' in elem['type']:
                            visual_component['title'] = visual_component['title'] + " " + elem['content'] if visual_component['title'] else elem['content']
                            visual_component['location'].append({'bbox': elem['bbox'], 'page': elem['page']})
                            visual_component['block_ids'].append(elem['id'])
                        elif 'footnote' in elem['type']:
                            visual_component['metadata'] = visual_component['metadata'] + " " + elem['content'] if visual_component['metadata'] else elem['content']
                            visual_component['location'].append({'bbox': elem['bbox'], 'page': elem['page']})
                            visual_component['block_ids'].append(elem['id'])
                visual_components.append(visual_component)

        for visual_component in visual_components:
            visual_component['children'] = []

        for visual_component in visual_components:
            for v_cp in visual_components:
                if visual_component['level'] in v_cp['block_ids']:
                    v_cp['children'].append(visual_component)
                    visual_components.remove(visual_component)

        def get_node_by_id(root, idx):
            if idx in root['block_ids']:
                return root
            for children in root['children']:
                check_child = get_node_by_id(children, idx)
                if check_child is not None:
                    return check_child
            return None

        def find_former_title(elements, idx):
            former_title = 0
            for element in elements:
                if element['type'] == 'title' and element['id'] < idx and element['id'] > former_title:
                    former_title = element['id']
            return former_title

        for visual_component in visual_components:

            idx = visual_component['level'] if visual_component['level'] >= 0 else find_former_title(elements, min(visual_component['block_ids']))
            tree_node = get_node_by_id(text_tree, idx)
            if tree_node:
                tree_node['children'].append(visual_component)

        return text_tree

    add_special_elements(text_tree, elements)

    def add_supplement(text_tree, elements):
        exist = []
        for element in elements:
            if element['type'] in supplement_types:
                title = f"Page {element['page']} - {element['type']}"
                cnt = 0
                while title in exist:
                    cnt += 1
                    title = f"Page {element['page']} - {element['type']} - {cnt}"
                exist.append(title)
                supp_component = cp_init(cp_type=element['type'], title=title, metadata=element['content'], content=element['content'], location=[{'bbox': element['bbox'], 'page': element['page']}], block_ids=[element['id']])
                text_tree['children'].append(supp_component)

    add_supplement(text_tree, elements)

    return text_tree
