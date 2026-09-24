const Z_CHARS = ["東", "南", "西", "北", "白", "發", "中"];

export function tileInfo(code) {
  if (code === 51) return { num: 5, suit: "m", aka: true, text: "5" };
  if (code === 52) return { num: 5, suit: "p", aka: true, text: "5" };
  if (code === 53) return { num: 5, suit: "s", aka: true, text: "5" };
  const suitIdx = Math.floor(code / 10) - 1;   // 0m 1p 2s 3z
  const num = code % 10;
  const suit = ["m", "p", "s", "z"][suitIdx];
  const text = suit === "z" ? Z_CHARS[num - 1] : String(num);
  return { num, suit, aka: false, text };
}

export function tileEl(code, cls = "") {
  const info = tileInfo(code);
  const el = document.createElement("span");
  el.className = `tile ${info.suit} ${info.aka ? "aka" : ""} ${cls}`.trim();
  const names = { m: "万", p: "筒", s: "索", z: "字牌" };
  const files = {
    m: ["Man1.svg", "Man2.svg", "Man3.svg", "Man4.svg", "Man5.svg",
        "Man6.svg", "Man7.svg", "Man8.svg", "Man9.svg"],
    p: ["Pin1.svg", "Pin2.svg", "Pin3.svg", "Pin4.svg", "Pin5.svg",
        "Pin6.svg", "Pin7.svg", "Pin8.svg", "Pin9.svg"],
    s: ["Sou1.svg", "Sou2.svg", "Sou3.svg", "Sou4.svg", "Sou5.svg",
        "Sou6.svg", "Sou7.svg", "Sou8.svg", "Sou9.svg"],
    z: ["Ton.svg", "Nan.svg", "Shaa.svg", "Pei.svg", "Haku.svg", "Hatsu.svg", "Chun.svg"],
  };
  const filename = info.aka
    ? `${info.suit === "m" ? "Man" : info.suit === "p" ? "Pin" : "Sou"}5-Dora.svg`
    : files[info.suit][info.num - 1];
  const image = document.createElement("img");
  image.src = `/static/vendor/mahjong/${filename}`;
  image.alt = info.suit === "z" ? info.text : `${info.num}${names[info.suit]}`;
  image.draggable = false;
  el.appendChild(image);
  el.title = image.alt;
  return el;
}

export function tileRow(codes, cls = "") {
  const row = document.createElement("span");
  row.className = "tile-row";
  (codes || []).forEach((c) => row.appendChild(typeof c === "number" ? tileEl(c, cls) : c));
  return row;
}

/** 解析摸牌序列符号 → {type, tiles[], label} */
export function parseDrawSymbol(sym) {
  if (typeof sym === "number") return { type: "tile", tiles: [sym], label: "" };
  const m = String(sym).match(/^c(\d{2})(\d{2})(\d{2})$/);       // 吃
  if (m) return { type: "call", tiles: [+m[1], +m[2], +m[3]], label: "吃" };
  const p = String(sym).match(/^p(\d{2})(\d{2})(\d{2})$|^(\d{2})p(\d{2})(\d{2})$|^(\d{2})(\d{2})p(\d{2})$/);
  if (p) {                                                        // 碰
    const nums = p.slice(1).filter(Boolean).map(Number);
    return { type: "call", tiles: nums, label: "碰" };
  }
  if (/[mk]/.test(sym)) {                                          // 杠
    const nums = sym.match(/\d{2}/g).map(Number);
    return { type: "call", tiles: nums, label: /k/.test(sym) ? "加杠" : "杠" };
  }
  if (/a/.test(sym)) {                                             // 暗杠
    const nums = sym.match(/\d{2}/g).map(Number);
    return { type: "call", tiles: nums, label: "暗杠" };
  }
  if (sym === "f44") return { type: "call", tiles: [44], label: "北" };
  return { type: "call", tiles: [], label: sym };
}

/** 将天凤/雀魂副露编码拆成牌面顺序；calledIndex 是被鸣入的牌位置。 */
export function parseMeldSymbol(sym) {
  const value = String(sym);
  if (value.startsWith("c")) {
    const nums = value.slice(1).match(/\d{2}/g)?.map(Number) || [];
    return { type: "chi", tiles: nums, calledIndex: 0 };
  }
  const tokens = value.match(/(?:[pmka])?\d{2}/g) || [];
  if (!tokens.length) return null;
  const tiles = tokens.map((token) => Number(token.slice(-2)));
  const marker = tokens.findIndex((token) => /^[pmk]/.test(token));
  const concealed = value.includes("a") && marker < 0;
  return {
    type: value.includes("a") ? "ankan" : value.includes("k") ? "kakan" : value.includes("m") ? "minkan" : "pon",
    tiles,
    calledIndex: concealed ? -1 : marker,
  };
}

function removeTile(hand, code) {
  const target = tileDeaka(code);
  const index = hand.findIndex((value) => tileDeaka(value) === target);
  if (index >= 0) hand.splice(index, 1);
}

function removeTiles(hand, codes) {
  codes.forEach((code) => removeTile(hand, code));
}

export function tileSortKey(code) {
  if (code === 51) return 15;
  if (code === 52) return 25;
  if (code === 53) return 35;
  return code;
}

function tileDeaka(code) {
  if (code === 51 || code === 52 || code === 53) {
    return 15 + (code - 51) * 10;
  }
  return code;
}

export function sortTiles(codes) {
  return codes.sort((a, b) => tileSortKey(a) - tileSortKey(b));
}

/** 将宝牌指示牌转换为实际宝牌；红5按普通5参与顺序计算。 */
export function doraTile(code) {
  const base = tileDeaka(code);
  const suit = Math.floor(base / 10);
  const num = base % 10;
  if (suit >= 1 && suit <= 3) {
    return suit * 10 + (num === 9 ? 1 : num + 1);
  }
  if (suit === 4) {
    const next = num === 4 ? 1 : num <= 3 ? num + 1 : num === 7 ? 5 : num + 1;
    return 40 + next;
  }
  return code;
}

export function doraTiles(codes) {
  return (codes || []).map(doraTile);
}

/** 从一局原始数据还原每家的终局暗手牌与副露。 */
export function finalHand(data, seat) {
  const hand = [...(data[4 + 3 * seat] || [])];
  const draws = data[5 + 3 * seat] || [];
  const discards = data[6 + 3 * seat] || [];
  const melds = [];
  let lastDrawTile = null;
  let lastDiscardTile = null;
  let discardIndex = 0;

  const consumeMeldDiscard = (value) => {
    if (typeof value !== "string" || !/[mka]/.test(value)) return;
    const meld = parseMeldSymbol(value);
    if (!meld) return;
    if (meld.type === "ankan") removeTiles(hand, meld.tiles);
    else if (meld.type === "kakan") {
      const addedTile = meld.tiles[meld.calledIndex >= 0 ? meld.calledIndex : meld.tiles.length - 1];
      removeTile(hand, addedTile);
      const pon = melds.find((item) => item.type === "pon"
        && item.tiles.some((code) => tileSortKey(code) === tileSortKey(addedTile)));
      if (pon) {
        pon.tiles.push(addedTile);
        return;
      }
    }
    melds.push(meld);
  };

  const consumeDiscard = (value, drawnTile = null) => {
    if (value === null || value === 0) return;
    const body = typeof value === "string" ? value.replace(/^r/, "") : value;
    const discardCode = Number(body);
    lastDiscardTile = discardCode === 60 ? drawnTile : discardCode;
    if (lastDiscardTile != null && !Number.isNaN(lastDiscardTile)) {
      removeTile(hand, lastDiscardTile);
    }
  };

  const nextDiscard = () => {
    while (discardIndex < discards.length) {
      const value = discards[discardIndex];
      if (value === 0) {
        discardIndex += 1;
        continue;
      }
      if (typeof value === "string" && !/^r?(?:\d{2}|60)$/.test(value)) {
        consumeMeldDiscard(value);
        discardIndex += 1;
        continue;
      }
      break;
    }
    return discardIndex < discards.length ? discards[discardIndex++] : null;
  };

  draws.forEach((draw) => {
    if (typeof draw === "number") {
      hand.push(draw);
      lastDrawTile = draw;
      const discard = nextDiscard();
      consumeDiscard(discard, draw);
      return;
    }
    const meld = parseMeldSymbol(draw);
    if (!meld) return;
    const concealedTiles = meld.calledIndex < 0
      ? meld.tiles
      : meld.tiles.filter((_, index) => index !== meld.calledIndex);
    removeTiles(hand, concealedTiles);
    melds.push(meld);
    // 吃/碰后会立即打牌；明杠则跳过这一步，等待岭上摸牌。
    if (meld.type === "chi" || meld.type === "pon") {
      consumeDiscard(nextDiscard());
    }
  });
  while (discardIndex < discards.length) {
    const discard = discards[discardIndex++];
    consumeMeldDiscard(discard);
  }
  return { hand: sortTiles(hand), melds, lastDrawTile, lastDiscardTile };
}

export function renderFinalHand(state) {
  const wrap = document.createElement("div");
  wrap.className = "final-hand";
  const concealed = document.createElement("div");
  concealed.className = "concealed-hand tile-row";
  const hand = [...state.hand];
  let winningTile = null;
  if (state.winningTile != null) {
    const index = hand.findIndex((code) => tileSortKey(code) === tileSortKey(state.winningTile));
    if (index >= 0) winningTile = hand.splice(index, 1)[0];
  }
  hand.forEach((code) => concealed.appendChild(tileEl(code)));
  wrap.appendChild(concealed);
  if (winningTile != null) {
    const winning = document.createElement("span");
    winning.className = "winning-tile-slot";
    winning.appendChild(tileEl(winningTile, "winning-tile"));
    wrap.appendChild(winning);
  }
  state.melds.forEach((meld) => {
    const group = document.createElement("span");
    group.className = "meld-group tile-row";
    meld.tiles.forEach((code, index) => {
      const tile = tileEl(code, index === meld.calledIndex ? `meld-called meld-called-${meld.calledIndex}` : "");
      group.appendChild(tile);
    });
    wrap.appendChild(group);
  });
  return wrap;
}

/** 渲染切牌序列（含摸切标记/立直标记/杠占位） */
export function renderDiscards(discards) {
  const wrap = document.createElement("span");
  wrap.className = "discard-row";
  (discards || []).forEach((d) => {
    if (d === 0) return;                       // 大明杠占位
    const el = document.createElement("span");
    el.className = "discard-item";
    if (typeof d === "string" && d.startsWith("r")) {
      const r = document.createElement("span");
      r.className = "tag riichi";
      r.textContent = "立直";
      el.appendChild(r);
      d = d.slice(1);
    }
    if (d === 60) {
      const back = document.createElement("span");
      back.className = "tile-back";
      const image = document.createElement("img");
      image.src = "/static/vendor/mahjong/Back.svg";
      image.alt = "牌背";
      image.draggable = false;
      back.appendChild(image);
      el.appendChild(back);
    } else {
      el.appendChild(tileEl(+d, "small"));
    }
    wrap.appendChild(el);
  });
  return wrap;
}
