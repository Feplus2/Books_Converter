// iOS 风开关
export function Toggle({
  checked,
  onChange,
  disabled,
}: {
  checked: boolean;
  onChange: (v: boolean) => void;
  disabled?: boolean;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      disabled={disabled}
      onClick={() => onChange(!checked)}
      className="relative h-[22px] w-[40px] shrink-0 cursor-pointer rounded-full transition-colors duration-150 disabled:cursor-not-allowed disabled:opacity-50"
      style={{
        background: checked
          ? "var(--accent)"
          : "color-mix(in srgb, var(--ink) 18%, transparent)",
      }}
    >
      <span
        className="absolute top-[2px] h-[18px] w-[18px] rounded-full bg-white transition-transform duration-150"
        style={{
          left: 2,
          transform: checked ? "translateX(18px)" : "translateX(0)",
          boxShadow: "0 1px 3px rgb(0 0 0 / 0.25)",
        }}
      />
    </button>
  );
}
