/** The README logo, served unchanged as the browser icon and navigation mark. */
export function BrandMark({ size = 34 }: { size?: number }) {
  return (
    <img
      className="brand-symbol"
      src={`${import.meta.env.BASE_URL}brand.svg`}
      width={size}
      height={size}
      alt=""
      aria-hidden="true"
    />
  );
}
