export const PAPER_BANNER = "HYPOTHETICAL / PAPER signals only. No broker orders";

export function PaperBanner({ extra }: { extra?: string }) {
  return (
    <p className="paper">
      <strong>{PAPER_BANNER}</strong>
      {extra ? <> {extra}</> : null}
    </p>
  );
}
