export default function ResultTable({ label, children }) {
  return (
    <div className="result-table" role="region" aria-label={label} tabIndex={0}>
      <table>{children}</table>
    </div>
  );
}
