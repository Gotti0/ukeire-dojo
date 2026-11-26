// 패 기호(1m, 5mr, E …)를 public/tiles의 파일 이름으로 바꾼다. 파일 이름은 Riichi-Trainer 원본 그대로다.
const HONOR_FILES = { E: "east", S: "south", W: "west", N: "north", P: "haku", F: "hatsu", C: "chun" };
const HONOR_NAMES = { E: "동", S: "남", W: "서", N: "북", P: "백", F: "발", C: "중" };
const SUITS = { m: "만", p: "통", s: "삭" };

export function tileImage(code) {
  if (HONOR_FILES[code]) return `/tiles/${HONOR_FILES[code]}.png`;
  if (code.endsWith("r")) return `/tiles/0${code[1]}.png`;
  return `/tiles/${code}.png`;
}

export function tileName(code) {
  if (HONOR_NAMES[code]) return HONOR_NAMES[code];
  return `${code.endsWith("r") ? "적" : ""}${code[0]}${SUITS[code[1]]}`;
}

/** 적5를 뗀 종류 기호. 채점은 종류 단위다. */
export function tileType(code) {
  return code.endsWith("r") ? code.slice(0, 2) : code;
}

const ORDER = "mpsESWNPFC";
export function sortTiles(codes) {
  const key = (c) => {
    const type = tileType(c);
    if (type.length === 1) return 30 + ORDER.indexOf(type);
    return ORDER.indexOf(type[1]) * 10 + Number(type[0]) + (c.endsWith("r") ? 0.5 : 0);
  };
  return [...codes].sort((a, b) => key(a) - key(b));
}

/** 도라 표시패가 가리키는 실제 도라. */
export function doraOf(indicator) {
  const type = tileType(indicator);
  if (type.length === 1) {
    const winds = "ESWN";
    const dragons = "PFC";
    if (winds.includes(type)) return winds[(winds.indexOf(type) + 1) % 4];
    return dragons[(dragons.indexOf(type) + 1) % 3];
  }
  return `${(Number(type[0]) % 9) + 1}${type[1]}`;
}
