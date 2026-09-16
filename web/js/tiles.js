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
  el.textContent = info.text;
  el.title = info.suit === "z" ? "字牌" : `${info.num}${info.suit}`;
  return el;
}

export function tileRow(codes, cls = "") {
  const row = document.createElement("span");
  row.style.display = "inline-flex";
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

/** 渲染切牌序列（含摸切标记/立直标记/杠占位） */
export function renderDiscards(discards) {
  const wrap = document.createElement("span");
  wrap.style.display = "inline-flex";
  wrap.style.flexWrap = "wrap";
  (discards || []).forEach((d) => {
    if (d === 0) return;                       // 大明杠占位
    const el = document.createElement("span");
    el.style.display = "inline-flex";
    el.style.alignItems = "center";
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
      el.appendChild(back);
    } else {
      el.appendChild(tileEl(+d, "small"));
    }
    wrap.appendChild(el);
  });
  return wrap;
}