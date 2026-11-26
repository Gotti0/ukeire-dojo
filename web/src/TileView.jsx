import { tileImage, tileName } from "./tiles.js";

export function Tile({ code, small, dim, sideways, onClick, marks = [], disabled, label }) {
  const className = ["tile", small && "small", dim && "dim", sideways && "sideways", ...marks].filter(Boolean).join(" ");
  const img = <img src={tileImage(code)} alt={tileName(code)} title={tileName(code)} draggable={false} />;
  if (!onClick) return <span className={className}>{img}</span>;
  return (
    <button
      type="button"
      className={className}
      onClick={onClick}
      disabled={disabled}
      aria-label={label || `${tileName(code)} 버리기`}
    >
      {img}
    </button>
  );
}

export function Melds({ melds }) {
  return (
    <span className="melds">
      {melds.map((meld, i) => (
        <span className="meld" key={i}>
          {meld.tiles.map((t, j) => (
            <Tile key={j} code={meld.type === "ANKAN" && (j === 0 || j === 3) ? "back" : t} small sideways={j === meld.calledIndex} />
          ))}
        </span>
      ))}
    </span>
  );
}
