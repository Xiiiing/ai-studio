/** Shared with the browser icon: three film frames opening into a play symbol. */
export function BrandMark({ size = 34 }: { size?: number }) {
  return (
    <svg
      className="brand-symbol"
      width={size}
      height={size}
      viewBox="0 0 36 36"
      aria-hidden="true"
      focusable="false"
    >
      <use href={`${import.meta.env.BASE_URL}brand.svg#mark`} />
    </svg>
  );
}
