var __defProp = Object.defineProperty;
var __defNormalProp = (obj, key, value) => key in obj ? __defProp(obj, key, { enumerable: true, configurable: true, writable: true, value }) : obj[key] = value;
var __publicField = (obj, key, value) => {
  __defNormalProp(obj, typeof key !== "symbol" ? key + "" : key, value);
  return value;
};
function simplify(t, e, i, s) {
  let r = s;
  const a = e + (i - e >> 1);
  let n, o = i - e;
  const h = t[e], c = t[e + 1], l = t[i], u = t[i + 1];
  for (let s2 = e + 3; s2 < i; s2 += 3) {
    const e2 = getSqSegDist(t[s2], t[s2 + 1], h, c, l, u);
    if (e2 > r)
      n = s2, r = e2;
    else if (e2 === r) {
      const t2 = Math.abs(s2 - a);
      t2 < o && (n = s2, o = t2);
    }
  }
  r > s && (n - e > 3 && simplify(t, e, n, s), t[n + 2] = r, i - n > 3 && simplify(t, n, i, s));
}
function getSqSegDist(t, e, i, s, r, a) {
  let n = r - i, o = a - s;
  if (0 !== n || 0 !== o) {
    const h = ((t - i) * n + (e - s) * o) / (n * n + o * o);
    h > 1 ? (i = r, s = a) : h > 0 && (i += n * h, s += o * h);
  }
  return n = t - i, o = e - s, n * n + o * o;
}
function createFeature(t, e, i, s) {
  const r = { id: null == t ? null : t, type: e, geometry: i, tags: s, minX: 1 / 0, minY: 1 / 0, maxX: -1 / 0, maxY: -1 / 0 };
  if ("Point" === e || "MultiPoint" === e || "LineString" === e)
    calcLineBBox(r, i);
  else if ("Polygon" === e)
    calcLineBBox(r, i[0]);
  else if ("MultiLineString" === e)
    for (const t2 of i)
      calcLineBBox(r, t2);
  else if ("MultiPolygon" === e)
    for (const t2 of i)
      calcLineBBox(r, t2[0]);
  return r;
}
function calcLineBBox(t, e) {
  for (let i = 0; i < e.length; i += 3)
    t.minX = Math.min(t.minX, e[i]), t.minY = Math.min(t.minY, e[i + 1]), t.maxX = Math.max(t.maxX, e[i]), t.maxY = Math.max(t.maxY, e[i + 1]);
}
function convert(t, e) {
  const i = [];
  if ("FeatureCollection" === t.type)
    for (let s = 0; s < t.features.length; s++)
      convertFeature(i, t.features[s], e, s);
  else
    "Feature" === t.type ? convertFeature(i, t, e) : convertFeature(i, { geometry: t }, e);
  return i;
}
function convertFeature(t, e, i, s) {
  if (!e.geometry)
    return;
  const r = e.geometry.coordinates;
  if (r && 0 === r.length)
    return;
  const a = e.geometry.type, n = Math.pow(i.tolerance / ((1 << i.maxZoom) * i.extent), 2);
  let o = [], h = e.id;
  if (i.promoteId ? h = e.properties[i.promoteId] : i.generateId && (h = s || 0), "Point" === a)
    convertPoint(r, o);
  else if ("MultiPoint" === a)
    for (const t2 of r)
      convertPoint(t2, o);
  else if ("LineString" === a)
    convertLine(r, o, n, false);
  else if ("MultiLineString" === a) {
    if (i.lineMetrics) {
      for (const i2 of r)
        o = [], convertLine(i2, o, n, false), t.push(createFeature(h, "LineString", o, e.properties));
      return;
    }
    convertLines(r, o, n, false);
  } else if ("Polygon" === a)
    convertLines(r, o, n, true);
  else {
    if ("MultiPolygon" !== a) {
      if ("GeometryCollection" === a) {
        for (const r2 of e.geometry.geometries)
          convertFeature(t, { id: h, geometry: r2, properties: e.properties }, i, s);
        return;
      }
      throw new Error("Input data is not a valid GeoJSON object.");
    }
    for (const t2 of r) {
      const e2 = [];
      convertLines(t2, e2, n, true), o.push(e2);
    }
  }
  t.push(createFeature(h, a, o, e.properties));
}
function convertPoint(t, e) {
  e.push(projectX(t[0]), projectY(t[1]), 0);
}
function convertLine(t, e, i, s) {
  let r, a, n = 0;
  for (let i2 = 0; i2 < t.length; i2++) {
    const o2 = projectX(t[i2][0]), h = projectY(t[i2][1]);
    e.push(o2, h, 0), i2 > 0 && (n += s ? (r * h - o2 * a) / 2 : Math.sqrt(Math.pow(o2 - r, 2) + Math.pow(h - a, 2))), r = o2, a = h;
  }
  const o = e.length - 3;
  e[2] = 1, simplify(e, 0, o, i), e[o + 2] = 1, e.size = Math.abs(n), e.start = 0, e.end = e.size;
}
function convertLines(t, e, i, s) {
  for (let r = 0; r < t.length; r++) {
    const a = [];
    convertLine(t[r], a, i, s), e.push(a);
  }
}
function projectX(t) {
  return t / 360 + 0.5;
}
function projectY(t) {
  const e = Math.sin(t * Math.PI / 180), i = 0.5 - 0.25 * Math.log((1 + e) / (1 - e)) / Math.PI;
  return i < 0 ? 0 : i > 1 ? 1 : i;
}
function clip(t, e, i, s, r, a, n, o) {
  if (s /= e, a >= (i /= e) && n < s)
    return t;
  if (n < i || a >= s)
    return null;
  const h = [];
  for (const e2 of t) {
    const t2 = e2.geometry;
    let a2 = e2.type;
    const n2 = 0 === r ? e2.minX : e2.minY, c = 0 === r ? e2.maxX : e2.maxY;
    if (n2 >= i && c < s) {
      h.push(e2);
      continue;
    }
    if (c < i || n2 >= s)
      continue;
    let l = [];
    if ("Point" === a2 || "MultiPoint" === a2)
      clipPoints(t2, l, i, s, r);
    else if ("LineString" === a2)
      clipLine(t2, l, i, s, r, false, o.lineMetrics);
    else if ("MultiLineString" === a2)
      clipLines(t2, l, i, s, r, false);
    else if ("Polygon" === a2)
      clipLines(t2, l, i, s, r, true);
    else if ("MultiPolygon" === a2)
      for (const e3 of t2) {
        const t3 = [];
        clipLines(e3, t3, i, s, r, true), t3.length && l.push(t3);
      }
    if (l.length) {
      if (o.lineMetrics && "LineString" === a2) {
        for (const t3 of l)
          h.push(createFeature(e2.id, a2, t3, e2.tags));
        continue;
      }
      "LineString" !== a2 && "MultiLineString" !== a2 || (1 === l.length ? (a2 = "LineString", l = l[0]) : a2 = "MultiLineString"), "Point" !== a2 && "MultiPoint" !== a2 || (a2 = 3 === l.length ? "Point" : "MultiPoint"), h.push(createFeature(e2.id, a2, l, e2.tags));
    }
  }
  return h.length ? h : null;
}
function clipPoints(t, e, i, s, r) {
  for (let a = 0; a < t.length; a += 3) {
    const n = t[a + r];
    n >= i && n <= s && addPoint(e, t[a], t[a + 1], t[a + 2]);
  }
}
function clipLine(t, e, i, s, r, a, n) {
  let o = newSlice(t);
  const h = 0 === r ? intersectX : intersectY;
  let c, l, u = t.start;
  for (let d2 = 0; d2 < t.length - 3; d2 += 3) {
    const m2 = t[d2], _2 = t[d2 + 1], f2 = t[d2 + 2], p2 = t[d2 + 3], x = t[d2 + 4], y = 0 === r ? m2 : _2, M = 0 === r ? p2 : x;
    let g = false;
    n && (c = Math.sqrt(Math.pow(m2 - p2, 2) + Math.pow(_2 - x, 2))), y < i ? M > i && (l = h(o, m2, _2, p2, x, i), n && (o.start = u + c * l)) : y > s ? M < s && (l = h(o, m2, _2, p2, x, s), n && (o.start = u + c * l)) : addPoint(o, m2, _2, f2), M < i && y >= i && (l = h(o, m2, _2, p2, x, i), g = true), M > s && y <= s && (l = h(o, m2, _2, p2, x, s), g = true), !a && g && (n && (o.end = u + c * l), e.push(o), o = newSlice(t)), n && (u += c);
  }
  let d = t.length - 3;
  const m = t[d], _ = t[d + 1], f = t[d + 2], p = 0 === r ? m : _;
  p >= i && p <= s && addPoint(o, m, _, f), d = o.length - 3, a && d >= 3 && (o[d] !== o[0] || o[d + 1] !== o[1]) && addPoint(o, o[0], o[1], o[2]), o.length && e.push(o);
}
function newSlice(t) {
  const e = [];
  return e.size = t.size, e.start = t.start, e.end = t.end, e;
}
function clipLines(t, e, i, s, r, a) {
  for (const n of t)
    clipLine(n, e, i, s, r, a, false);
}
function addPoint(t, e, i, s) {
  t.push(e, i, s);
}
function intersectX(t, e, i, s, r, a) {
  const n = (a - e) / (s - e);
  return addPoint(t, a, i + (r - i) * n, 1), n;
}
function intersectY(t, e, i, s, r, a) {
  const n = (a - i) / (r - i);
  return addPoint(t, e + (s - e) * n, a, 1), n;
}
function wrap(t, e) {
  const i = e.buffer / e.extent;
  let s = t;
  const r = clip(t, 1, -1 - i, i, 0, -1, 2, e), a = clip(t, 1, 1 - i, 2 + i, 0, -1, 2, e);
  return (r || a) && (s = clip(t, 1, -i, 1 + i, 0, -1, 2, e) || [], r && (s = shiftFeatureCoords(r, 1).concat(s)), a && (s = s.concat(shiftFeatureCoords(a, -1)))), s;
}
function shiftFeatureCoords(t, e) {
  const i = [];
  for (let s = 0; s < t.length; s++) {
    const r = t[s], a = r.type;
    let n;
    if ("Point" === a || "MultiPoint" === a || "LineString" === a)
      n = shiftCoords(r.geometry, e);
    else if ("MultiLineString" === a || "Polygon" === a) {
      n = [];
      for (const t2 of r.geometry)
        n.push(shiftCoords(t2, e));
    } else if ("MultiPolygon" === a) {
      n = [];
      for (const t2 of r.geometry) {
        const i2 = [];
        for (const s2 of t2)
          i2.push(shiftCoords(s2, e));
        n.push(i2);
      }
    }
    i.push(createFeature(r.id, a, n, r.tags));
  }
  return i;
}
function shiftCoords(t, e) {
  const i = [];
  i.size = t.size, void 0 !== t.start && (i.start = t.start, i.end = t.end);
  for (let s = 0; s < t.length; s += 3)
    i.push(t[s] + e, t[s + 1], t[s + 2]);
  return i;
}
function transformTile(t, e) {
  if (t.transformed)
    return t;
  const i = 1 << t.z, s = t.x, r = t.y;
  for (const a of t.features) {
    const t2 = a.geometry, n = a.type;
    if (a.geometry = [], 1 === n)
      for (let n2 = 0; n2 < t2.length; n2 += 2)
        a.geometry.push(transformPoint(t2[n2], t2[n2 + 1], e, i, s, r));
    else
      for (let n2 = 0; n2 < t2.length; n2++) {
        const o = [];
        for (let a2 = 0; a2 < t2[n2].length; a2 += 2)
          o.push(transformPoint(t2[n2][a2], t2[n2][a2 + 1], e, i, s, r));
        a.geometry.push(o);
      }
  }
  return t.transformed = true, t;
}
function transformPoint(t, e, i, s, r, a) {
  return [Math.round(i * (t * s - r)), Math.round(i * (e * s - a))];
}
function createTile(t, e, i, s, r) {
  const a = e === r.maxZoom ? 0 : r.tolerance / ((1 << e) * r.extent), n = { features: [], numPoints: 0, numSimplified: 0, numFeatures: t.length, source: null, x: i, y: s, z: e, transformed: false, minX: 2, minY: 1, maxX: -1, maxY: 0 };
  for (const e2 of t)
    addFeature(n, e2, a, r);
  return n;
}
function addFeature(t, e, i, s) {
  const r = e.geometry, a = e.type, n = [];
  if (t.minX = Math.min(t.minX, e.minX), t.minY = Math.min(t.minY, e.minY), t.maxX = Math.max(t.maxX, e.maxX), t.maxY = Math.max(t.maxY, e.maxY), "Point" === a || "MultiPoint" === a)
    for (let e2 = 0; e2 < r.length; e2 += 3)
      n.push(r[e2], r[e2 + 1]), t.numPoints++, t.numSimplified++;
  else if ("LineString" === a)
    addLine(n, r, t, i, false, false);
  else if ("MultiLineString" === a || "Polygon" === a)
    for (let e2 = 0; e2 < r.length; e2++)
      addLine(n, r[e2], t, i, "Polygon" === a, 0 === e2);
  else if ("MultiPolygon" === a)
    for (let e2 = 0; e2 < r.length; e2++) {
      const s2 = r[e2];
      for (let e3 = 0; e3 < s2.length; e3++)
        addLine(n, s2[e3], t, i, true, 0 === e3);
    }
  if (n.length) {
    let i2 = e.tags || null;
    if ("LineString" === a && s.lineMetrics) {
      i2 = {};
      for (const t2 in e.tags)
        i2[t2] = e.tags[t2];
      i2.mapbox_clip_start = r.start / r.size, i2.mapbox_clip_end = r.end / r.size;
    }
    const o = { geometry: n, type: "Polygon" === a || "MultiPolygon" === a ? 3 : "LineString" === a || "MultiLineString" === a ? 2 : 1, tags: i2 };
    null !== e.id && (o.id = e.id), t.features.push(o);
  }
}
function addLine(t, e, i, s, r, a) {
  const n = s * s;
  if (s > 0 && e.size < (r ? n : s))
    return void (i.numPoints += e.length / 3);
  const o = [];
  for (let t2 = 0; t2 < e.length; t2 += 3)
    (0 === s || e[t2 + 2] > n) && (i.numSimplified++, o.push(e[t2], e[t2 + 1])), i.numPoints++;
  r && rewind(o, a), t.push(o);
}
function rewind(t, e) {
  let i = 0;
  for (let e2 = 0, s = t.length, r = s - 2; e2 < s; r = e2, e2 += 2)
    i += (t[e2] - t[r]) * (t[e2 + 1] + t[r + 1]);
  if (i > 0 === e)
    for (let e2 = 0, i2 = t.length; e2 < i2 / 2; e2 += 2) {
      const s = t[e2], r = t[e2 + 1];
      t[e2] = t[i2 - 2 - e2], t[e2 + 1] = t[i2 - 1 - e2], t[i2 - 2 - e2] = s, t[i2 - 1 - e2] = r;
    }
}
const defaultOptions = { maxZoom: 14, indexMaxZoom: 5, indexMaxPoints: 1e5, tolerance: 3, extent: 4096, buffer: 64, lineMetrics: false, promoteId: null, generateId: false, debug: 0 };
class GeoJSONVT {
  constructor(t, e) {
    const i = (e = this.options = extend$1(Object.create(defaultOptions), e)).debug;
    if (i && console.time("preprocess data"), e.maxZoom < 0 || e.maxZoom > 24)
      throw new Error("maxZoom should be in the 0-24 range");
    if (e.promoteId && e.generateId)
      throw new Error("promoteId and generateId cannot be used together.");
    let s = convert(t, e);
    this.tiles = {}, this.tileCoords = [], i && (console.timeEnd("preprocess data"), console.log("index: maxZoom: %d, maxPoints: %d", e.indexMaxZoom, e.indexMaxPoints), console.time("generate tiles"), this.stats = {}, this.total = 0), s = wrap(s, e), s.length && this.splitTile(s, 0, 0, 0), i && (s.length && console.log("features: %d, points: %d", this.tiles[0].numFeatures, this.tiles[0].numPoints), console.timeEnd("generate tiles"), console.log("tiles generated:", this.total, JSON.stringify(this.stats)));
  }
  splitTile(t, e, i, s, r, a, n) {
    const o = [t, e, i, s], h = this.options, c = h.debug;
    for (; o.length; ) {
      s = o.pop(), i = o.pop(), e = o.pop(), t = o.pop();
      const l = 1 << e, u = toID(e, i, s);
      let d = this.tiles[u];
      if (!d && (c > 1 && console.time("creation"), d = this.tiles[u] = createTile(t, e, i, s, h), this.tileCoords.push({ z: e, x: i, y: s }), c)) {
        c > 1 && (console.log("tile z%d-%d-%d (features: %d, points: %d, simplified: %d)", e, i, s, d.numFeatures, d.numPoints, d.numSimplified), console.timeEnd("creation"));
        const t2 = `z${e}`;
        this.stats[t2] = (this.stats[t2] || 0) + 1, this.total++;
      }
      if (d.source = t, null == r) {
        if (e === h.indexMaxZoom || d.numPoints <= h.indexMaxPoints)
          continue;
      } else {
        if (e === h.maxZoom || e === r)
          continue;
        if (null != r) {
          const t2 = r - e;
          if (i !== a >> t2 || s !== n >> t2)
            continue;
        }
      }
      if (d.source = null, 0 === t.length)
        continue;
      c > 1 && console.time("clipping");
      const m = 0.5 * h.buffer / h.extent, _ = 0.5 - m, f = 0.5 + m, p = 1 + m;
      let x = null, y = null, M = null, g = null, w = clip(t, l, i - m, i + f, 0, d.minX, d.maxX, h), P = clip(t, l, i + _, i + p, 0, d.minX, d.maxX, h);
      t = null, w && (x = clip(w, l, s - m, s + f, 1, d.minY, d.maxY, h), y = clip(w, l, s + _, s + p, 1, d.minY, d.maxY, h), w = null), P && (M = clip(P, l, s - m, s + f, 1, d.minY, d.maxY, h), g = clip(P, l, s + _, s + p, 1, d.minY, d.maxY, h), P = null), c > 1 && console.timeEnd("clipping"), o.push(x || [], e + 1, 2 * i, 2 * s), o.push(y || [], e + 1, 2 * i, 2 * s + 1), o.push(M || [], e + 1, 2 * i + 1, 2 * s), o.push(g || [], e + 1, 2 * i + 1, 2 * s + 1);
    }
  }
  getTile(t, e, i) {
    t = +t, e = +e, i = +i;
    const s = this.options, { extent: r, debug: a } = s;
    if (t < 0 || t > 24)
      return null;
    const n = 1 << t, o = toID(t, e = e + n & n - 1, i);
    if (this.tiles[o])
      return transformTile(this.tiles[o], r);
    a > 1 && console.log("drilling down to z%d-%d-%d", t, e, i);
    let h, c = t, l = e, u = i;
    for (; !h && c > 0; )
      c--, l >>= 1, u >>= 1, h = this.tiles[toID(c, l, u)];
    return h && h.source ? (a > 1 && (console.log("found parent tile z%d-%d-%d", c, l, u), console.time("drilling down")), this.splitTile(h.source, c, l, u, t, e, i), a > 1 && console.timeEnd("drilling down"), this.tiles[o] ? transformTile(this.tiles[o], r) : null) : null;
  }
}
function toID(t, e, i) {
  return 32 * ((1 << t) * i + e) + t;
}
function extend$1(t, e) {
  for (const i in e)
    t[i] = e[i];
  return t;
}
function geojsonvt(t, e) {
  return new GeoJSONVT(t, e);
}
/**
 * @license
 * Copyright 2010-2025 Three.js Authors
 * SPDX-License-Identifier: MIT
 */
const REVISION = "179", WebGLCoordinateSystem = 2e3, WebGPUCoordinateSystem = 2001, _lut = ["00", "01", "02", "03", "04", "05", "06", "07", "08", "09", "0a", "0b", "0c", "0d", "0e", "0f", "10", "11", "12", "13", "14", "15", "16", "17", "18", "19", "1a", "1b", "1c", "1d", "1e", "1f", "20", "21", "22", "23", "24", "25", "26", "27", "28", "29", "2a", "2b", "2c", "2d", "2e", "2f", "30", "31", "32", "33", "34", "35", "36", "37", "38", "39", "3a", "3b", "3c", "3d", "3e", "3f", "40", "41", "42", "43", "44", "45", "46", "47", "48", "49", "4a", "4b", "4c", "4d", "4e", "4f", "50", "51", "52", "53", "54", "55", "56", "57", "58", "59", "5a", "5b", "5c", "5d", "5e", "5f", "60", "61", "62", "63", "64", "65", "66", "67", "68", "69", "6a", "6b", "6c", "6d", "6e", "6f", "70", "71", "72", "73", "74", "75", "76", "77", "78", "79", "7a", "7b", "7c", "7d", "7e", "7f", "80", "81", "82", "83", "84", "85", "86", "87", "88", "89", "8a", "8b", "8c", "8d", "8e", "8f", "90", "91", "92", "93", "94", "95", "96", "97", "98", "99", "9a", "9b", "9c", "9d", "9e", "9f", "a0", "a1", "a2", "a3", "a4", "a5", "a6", "a7", "a8", "a9", "aa", "ab", "ac", "ad", "ae", "af", "b0", "b1", "b2", "b3", "b4", "b5", "b6", "b7", "b8", "b9", "ba", "bb", "bc", "bd", "be", "bf", "c0", "c1", "c2", "c3", "c4", "c5", "c6", "c7", "c8", "c9", "ca", "cb", "cc", "cd", "ce", "cf", "d0", "d1", "d2", "d3", "d4", "d5", "d6", "d7", "d8", "d9", "da", "db", "dc", "dd", "de", "df", "e0", "e1", "e2", "e3", "e4", "e5", "e6", "e7", "e8", "e9", "ea", "eb", "ec", "ed", "ee", "ef", "f0", "f1", "f2", "f3", "f4", "f5", "f6", "f7", "f8", "f9", "fa", "fb", "fc", "fd", "fe", "ff"];
let _seed = 1234567;
const DEG2RAD = Math.PI / 180, RAD2DEG = 180 / Math.PI;
function generateUUID() {
  const t = 4294967295 * Math.random() | 0, e = 4294967295 * Math.random() | 0, i = 4294967295 * Math.random() | 0, s = 4294967295 * Math.random() | 0;
  return (_lut[255 & t] + _lut[t >> 8 & 255] + _lut[t >> 16 & 255] + _lut[t >> 24 & 255] + "-" + _lut[255 & e] + _lut[e >> 8 & 255] + "-" + _lut[e >> 16 & 15 | 64] + _lut[e >> 24 & 255] + "-" + _lut[63 & i | 128] + _lut[i >> 8 & 255] + "-" + _lut[i >> 16 & 255] + _lut[i >> 24 & 255] + _lut[255 & s] + _lut[s >> 8 & 255] + _lut[s >> 16 & 255] + _lut[s >> 24 & 255]).toLowerCase();
}
function clamp$1(t, e, i) {
  return Math.max(e, Math.min(i, t));
}
function euclideanModulo(t, e) {
  return (t % e + e) % e;
}
function mapLinear(t, e, i, s, r) {
  return s + (t - e) * (r - s) / (i - e);
}
function inverseLerp(t, e, i) {
  return t !== e ? (i - t) / (e - t) : 0;
}
function lerp(t, e, i) {
  return (1 - i) * t + i * e;
}
function damp(t, e, i, s) {
  return lerp(t, e, 1 - Math.exp(-i * s));
}
function pingpong(t, e = 1) {
  return e - Math.abs(euclideanModulo(t, 2 * e) - e);
}
function smoothstep(t, e, i) {
  return t <= e ? 0 : t >= i ? 1 : (t = (t - e) / (i - e)) * t * (3 - 2 * t);
}
function smootherstep(t, e, i) {
  return t <= e ? 0 : t >= i ? 1 : (t = (t - e) / (i - e)) * t * t * (t * (6 * t - 15) + 10);
}
function randInt(t, e) {
  return t + Math.floor(Math.random() * (e - t + 1));
}
function randFloat(t, e) {
  return t + Math.random() * (e - t);
}
function randFloatSpread(t) {
  return t * (0.5 - Math.random());
}
function seededRandom(t) {
  void 0 !== t && (_seed = t);
  let e = _seed += 1831565813;
  return e = Math.imul(e ^ e >>> 15, 1 | e), e ^= e + Math.imul(e ^ e >>> 7, 61 | e), ((e ^ e >>> 14) >>> 0) / 4294967296;
}
function degToRad(t) {
  return t * DEG2RAD;
}
function radToDeg(t) {
  return t * RAD2DEG;
}
function isPowerOfTwo(t) {
  return 0 == (t & t - 1) && 0 !== t;
}
function ceilPowerOfTwo(t) {
  return Math.pow(2, Math.ceil(Math.log(t) / Math.LN2));
}
function floorPowerOfTwo(t) {
  return Math.pow(2, Math.floor(Math.log(t) / Math.LN2));
}
function setQuaternionFromProperEuler(t, e, i, s, r) {
  const a = Math.cos, n = Math.sin, o = a(i / 2), h = n(i / 2), c = a((e + s) / 2), l = n((e + s) / 2), u = a((e - s) / 2), d = n((e - s) / 2), m = a((s - e) / 2), _ = n((s - e) / 2);
  switch (r) {
    case "XYX":
      t.set(o * l, h * u, h * d, o * c);
      break;
    case "YZY":
      t.set(h * d, o * l, h * u, o * c);
      break;
    case "ZXZ":
      t.set(h * u, h * d, o * l, o * c);
      break;
    case "XZX":
      t.set(o * l, h * _, h * m, o * c);
      break;
    case "YXY":
      t.set(h * m, o * l, h * _, o * c);
      break;
    case "ZYZ":
      t.set(h * _, h * m, o * l, o * c);
      break;
    default:
      console.warn("THREE.MathUtils: .setQuaternionFromProperEuler() encountered an unknown order: " + r);
  }
}
function denormalize(t, e) {
  switch (e.constructor) {
    case Float32Array:
      return t;
    case Uint32Array:
      return t / 4294967295;
    case Uint16Array:
      return t / 65535;
    case Uint8Array:
      return t / 255;
    case Int32Array:
      return Math.max(t / 2147483647, -1);
    case Int16Array:
      return Math.max(t / 32767, -1);
    case Int8Array:
      return Math.max(t / 127, -1);
    default:
      throw new Error("Invalid component type.");
  }
}
function normalize(t, e) {
  switch (e.constructor) {
    case Float32Array:
      return t;
    case Uint32Array:
      return Math.round(4294967295 * t);
    case Uint16Array:
      return Math.round(65535 * t);
    case Uint8Array:
      return Math.round(255 * t);
    case Int32Array:
      return Math.round(2147483647 * t);
    case Int16Array:
      return Math.round(32767 * t);
    case Int8Array:
      return Math.round(127 * t);
    default:
      throw new Error("Invalid component type.");
  }
}
const MathUtils = { DEG2RAD, RAD2DEG, generateUUID, clamp: clamp$1, euclideanModulo, mapLinear, inverseLerp, lerp, damp, pingpong, smoothstep, smootherstep, randInt, randFloat, randFloatSpread, seededRandom, degToRad, radToDeg, isPowerOfTwo, ceilPowerOfTwo, floorPowerOfTwo, setQuaternionFromProperEuler, normalize, denormalize };
class Vector2 {
  constructor(t = 0, e = 0) {
    Vector2.prototype.isVector2 = true, this.x = t, this.y = e;
  }
  get width() {
    return this.x;
  }
  set width(t) {
    this.x = t;
  }
  get height() {
    return this.y;
  }
  set height(t) {
    this.y = t;
  }
  set(t, e) {
    return this.x = t, this.y = e, this;
  }
  setScalar(t) {
    return this.x = t, this.y = t, this;
  }
  setX(t) {
    return this.x = t, this;
  }
  setY(t) {
    return this.y = t, this;
  }
  setComponent(t, e) {
    switch (t) {
      case 0:
        this.x = e;
        break;
      case 1:
        this.y = e;
        break;
      default:
        throw new Error("index is out of range: " + t);
    }
    return this;
  }
  getComponent(t) {
    switch (t) {
      case 0:
        return this.x;
      case 1:
        return this.y;
      default:
        throw new Error("index is out of range: " + t);
    }
  }
  clone() {
    return new this.constructor(this.x, this.y);
  }
  copy(t) {
    return this.x = t.x, this.y = t.y, this;
  }
  add(t) {
    return this.x += t.x, this.y += t.y, this;
  }
  addScalar(t) {
    return this.x += t, this.y += t, this;
  }
  addVectors(t, e) {
    return this.x = t.x + e.x, this.y = t.y + e.y, this;
  }
  addScaledVector(t, e) {
    return this.x += t.x * e, this.y += t.y * e, this;
  }
  sub(t) {
    return this.x -= t.x, this.y -= t.y, this;
  }
  subScalar(t) {
    return this.x -= t, this.y -= t, this;
  }
  subVectors(t, e) {
    return this.x = t.x - e.x, this.y = t.y - e.y, this;
  }
  multiply(t) {
    return this.x *= t.x, this.y *= t.y, this;
  }
  multiplyScalar(t) {
    return this.x *= t, this.y *= t, this;
  }
  divide(t) {
    return this.x /= t.x, this.y /= t.y, this;
  }
  divideScalar(t) {
    return this.multiplyScalar(1 / t);
  }
  applyMatrix3(t) {
    const e = this.x, i = this.y, s = t.elements;
    return this.x = s[0] * e + s[3] * i + s[6], this.y = s[1] * e + s[4] * i + s[7], this;
  }
  min(t) {
    return this.x = Math.min(this.x, t.x), this.y = Math.min(this.y, t.y), this;
  }
  max(t) {
    return this.x = Math.max(this.x, t.x), this.y = Math.max(this.y, t.y), this;
  }
  clamp(t, e) {
    return this.x = clamp$1(this.x, t.x, e.x), this.y = clamp$1(this.y, t.y, e.y), this;
  }
  clampScalar(t, e) {
    return this.x = clamp$1(this.x, t, e), this.y = clamp$1(this.y, t, e), this;
  }
  clampLength(t, e) {
    const i = this.length();
    return this.divideScalar(i || 1).multiplyScalar(clamp$1(i, t, e));
  }
  floor() {
    return this.x = Math.floor(this.x), this.y = Math.floor(this.y), this;
  }
  ceil() {
    return this.x = Math.ceil(this.x), this.y = Math.ceil(this.y), this;
  }
  round() {
    return this.x = Math.round(this.x), this.y = Math.round(this.y), this;
  }
  roundToZero() {
    return this.x = Math.trunc(this.x), this.y = Math.trunc(this.y), this;
  }
  negate() {
    return this.x = -this.x, this.y = -this.y, this;
  }
  dot(t) {
    return this.x * t.x + this.y * t.y;
  }
  cross(t) {
    return this.x * t.y - this.y * t.x;
  }
  lengthSq() {
    return this.x * this.x + this.y * this.y;
  }
  length() {
    return Math.sqrt(this.x * this.x + this.y * this.y);
  }
  manhattanLength() {
    return Math.abs(this.x) + Math.abs(this.y);
  }
  normalize() {
    return this.divideScalar(this.length() || 1);
  }
  angle() {
    return Math.atan2(-this.y, -this.x) + Math.PI;
  }
  angleTo(t) {
    const e = Math.sqrt(this.lengthSq() * t.lengthSq());
    if (0 === e)
      return Math.PI / 2;
    const i = this.dot(t) / e;
    return Math.acos(clamp$1(i, -1, 1));
  }
  distanceTo(t) {
    return Math.sqrt(this.distanceToSquared(t));
  }
  distanceToSquared(t) {
    const e = this.x - t.x, i = this.y - t.y;
    return e * e + i * i;
  }
  manhattanDistanceTo(t) {
    return Math.abs(this.x - t.x) + Math.abs(this.y - t.y);
  }
  setLength(t) {
    return this.normalize().multiplyScalar(t);
  }
  lerp(t, e) {
    return this.x += (t.x - this.x) * e, this.y += (t.y - this.y) * e, this;
  }
  lerpVectors(t, e, i) {
    return this.x = t.x + (e.x - t.x) * i, this.y = t.y + (e.y - t.y) * i, this;
  }
  equals(t) {
    return t.x === this.x && t.y === this.y;
  }
  fromArray(t, e = 0) {
    return this.x = t[e], this.y = t[e + 1], this;
  }
  toArray(t = [], e = 0) {
    return t[e] = this.x, t[e + 1] = this.y, t;
  }
  fromBufferAttribute(t, e) {
    return this.x = t.getX(e), this.y = t.getY(e), this;
  }
  rotateAround(t, e) {
    const i = Math.cos(e), s = Math.sin(e), r = this.x - t.x, a = this.y - t.y;
    return this.x = r * i - a * s + t.x, this.y = r * s + a * i + t.y, this;
  }
  random() {
    return this.x = Math.random(), this.y = Math.random(), this;
  }
  *[Symbol.iterator]() {
    yield this.x, yield this.y;
  }
}
class Quaternion$1 {
  constructor(t = 0, e = 0, i = 0, s = 1) {
    this.isQuaternion = true, this._x = t, this._y = e, this._z = i, this._w = s;
  }
  static slerpFlat(t, e, i, s, r, a, n) {
    let o = i[s + 0], h = i[s + 1], c = i[s + 2], l = i[s + 3];
    const u = r[a + 0], d = r[a + 1], m = r[a + 2], _ = r[a + 3];
    if (0 === n)
      return t[e + 0] = o, t[e + 1] = h, t[e + 2] = c, void (t[e + 3] = l);
    if (1 === n)
      return t[e + 0] = u, t[e + 1] = d, t[e + 2] = m, void (t[e + 3] = _);
    if (l !== _ || o !== u || h !== d || c !== m) {
      let t2 = 1 - n;
      const e2 = o * u + h * d + c * m + l * _, i2 = e2 >= 0 ? 1 : -1, s2 = 1 - e2 * e2;
      if (s2 > Number.EPSILON) {
        const r3 = Math.sqrt(s2), a2 = Math.atan2(r3, e2 * i2);
        t2 = Math.sin(t2 * a2) / r3, n = Math.sin(n * a2) / r3;
      }
      const r2 = n * i2;
      if (o = o * t2 + u * r2, h = h * t2 + d * r2, c = c * t2 + m * r2, l = l * t2 + _ * r2, t2 === 1 - n) {
        const t3 = 1 / Math.sqrt(o * o + h * h + c * c + l * l);
        o *= t3, h *= t3, c *= t3, l *= t3;
      }
    }
    t[e] = o, t[e + 1] = h, t[e + 2] = c, t[e + 3] = l;
  }
  static multiplyQuaternionsFlat(t, e, i, s, r, a) {
    const n = i[s], o = i[s + 1], h = i[s + 2], c = i[s + 3], l = r[a], u = r[a + 1], d = r[a + 2], m = r[a + 3];
    return t[e] = n * m + c * l + o * d - h * u, t[e + 1] = o * m + c * u + h * l - n * d, t[e + 2] = h * m + c * d + n * u - o * l, t[e + 3] = c * m - n * l - o * u - h * d, t;
  }
  get x() {
    return this._x;
  }
  set x(t) {
    this._x = t, this._onChangeCallback();
  }
  get y() {
    return this._y;
  }
  set y(t) {
    this._y = t, this._onChangeCallback();
  }
  get z() {
    return this._z;
  }
  set z(t) {
    this._z = t, this._onChangeCallback();
  }
  get w() {
    return this._w;
  }
  set w(t) {
    this._w = t, this._onChangeCallback();
  }
  set(t, e, i, s) {
    return this._x = t, this._y = e, this._z = i, this._w = s, this._onChangeCallback(), this;
  }
  clone() {
    return new this.constructor(this._x, this._y, this._z, this._w);
  }
  copy(t) {
    return this._x = t.x, this._y = t.y, this._z = t.z, this._w = t.w, this._onChangeCallback(), this;
  }
  setFromEuler(t, e = true) {
    const i = t._x, s = t._y, r = t._z, a = t._order, n = Math.cos, o = Math.sin, h = n(i / 2), c = n(s / 2), l = n(r / 2), u = o(i / 2), d = o(s / 2), m = o(r / 2);
    switch (a) {
      case "XYZ":
        this._x = u * c * l + h * d * m, this._y = h * d * l - u * c * m, this._z = h * c * m + u * d * l, this._w = h * c * l - u * d * m;
        break;
      case "YXZ":
        this._x = u * c * l + h * d * m, this._y = h * d * l - u * c * m, this._z = h * c * m - u * d * l, this._w = h * c * l + u * d * m;
        break;
      case "ZXY":
        this._x = u * c * l - h * d * m, this._y = h * d * l + u * c * m, this._z = h * c * m + u * d * l, this._w = h * c * l - u * d * m;
        break;
      case "ZYX":
        this._x = u * c * l - h * d * m, this._y = h * d * l + u * c * m, this._z = h * c * m - u * d * l, this._w = h * c * l + u * d * m;
        break;
      case "YZX":
        this._x = u * c * l + h * d * m, this._y = h * d * l + u * c * m, this._z = h * c * m - u * d * l, this._w = h * c * l - u * d * m;
        break;
      case "XZY":
        this._x = u * c * l - h * d * m, this._y = h * d * l - u * c * m, this._z = h * c * m + u * d * l, this._w = h * c * l + u * d * m;
        break;
      default:
        console.warn("THREE.Quaternion: .setFromEuler() encountered an unknown order: " + a);
    }
    return true === e && this._onChangeCallback(), this;
  }
  setFromAxisAngle(t, e) {
    const i = e / 2, s = Math.sin(i);
    return this._x = t.x * s, this._y = t.y * s, this._z = t.z * s, this._w = Math.cos(i), this._onChangeCallback(), this;
  }
  setFromRotationMatrix(t) {
    const e = t.elements, i = e[0], s = e[4], r = e[8], a = e[1], n = e[5], o = e[9], h = e[2], c = e[6], l = e[10], u = i + n + l;
    if (u > 0) {
      const t2 = 0.5 / Math.sqrt(u + 1);
      this._w = 0.25 / t2, this._x = (c - o) * t2, this._y = (r - h) * t2, this._z = (a - s) * t2;
    } else if (i > n && i > l) {
      const t2 = 2 * Math.sqrt(1 + i - n - l);
      this._w = (c - o) / t2, this._x = 0.25 * t2, this._y = (s + a) / t2, this._z = (r + h) / t2;
    } else if (n > l) {
      const t2 = 2 * Math.sqrt(1 + n - i - l);
      this._w = (r - h) / t2, this._x = (s + a) / t2, this._y = 0.25 * t2, this._z = (o + c) / t2;
    } else {
      const t2 = 2 * Math.sqrt(1 + l - i - n);
      this._w = (a - s) / t2, this._x = (r + h) / t2, this._y = (o + c) / t2, this._z = 0.25 * t2;
    }
    return this._onChangeCallback(), this;
  }
  setFromUnitVectors(t, e) {
    let i = t.dot(e) + 1;
    return i < 1e-8 ? (i = 0, Math.abs(t.x) > Math.abs(t.z) ? (this._x = -t.y, this._y = t.x, this._z = 0, this._w = i) : (this._x = 0, this._y = -t.z, this._z = t.y, this._w = i)) : (this._x = t.y * e.z - t.z * e.y, this._y = t.z * e.x - t.x * e.z, this._z = t.x * e.y - t.y * e.x, this._w = i), this.normalize();
  }
  angleTo(t) {
    return 2 * Math.acos(Math.abs(clamp$1(this.dot(t), -1, 1)));
  }
  rotateTowards(t, e) {
    const i = this.angleTo(t);
    if (0 === i)
      return this;
    const s = Math.min(1, e / i);
    return this.slerp(t, s), this;
  }
  identity() {
    return this.set(0, 0, 0, 1);
  }
  invert() {
    return this.conjugate();
  }
  conjugate() {
    return this._x *= -1, this._y *= -1, this._z *= -1, this._onChangeCallback(), this;
  }
  dot(t) {
    return this._x * t._x + this._y * t._y + this._z * t._z + this._w * t._w;
  }
  lengthSq() {
    return this._x * this._x + this._y * this._y + this._z * this._z + this._w * this._w;
  }
  length() {
    return Math.sqrt(this._x * this._x + this._y * this._y + this._z * this._z + this._w * this._w);
  }
  normalize() {
    let t = this.length();
    return 0 === t ? (this._x = 0, this._y = 0, this._z = 0, this._w = 1) : (t = 1 / t, this._x = this._x * t, this._y = this._y * t, this._z = this._z * t, this._w = this._w * t), this._onChangeCallback(), this;
  }
  multiply(t) {
    return this.multiplyQuaternions(this, t);
  }
  premultiply(t) {
    return this.multiplyQuaternions(t, this);
  }
  multiplyQuaternions(t, e) {
    const i = t._x, s = t._y, r = t._z, a = t._w, n = e._x, o = e._y, h = e._z, c = e._w;
    return this._x = i * c + a * n + s * h - r * o, this._y = s * c + a * o + r * n - i * h, this._z = r * c + a * h + i * o - s * n, this._w = a * c - i * n - s * o - r * h, this._onChangeCallback(), this;
  }
  slerp(t, e) {
    if (0 === e)
      return this;
    if (1 === e)
      return this.copy(t);
    const i = this._x, s = this._y, r = this._z, a = this._w;
    let n = a * t._w + i * t._x + s * t._y + r * t._z;
    if (n < 0 ? (this._w = -t._w, this._x = -t._x, this._y = -t._y, this._z = -t._z, n = -n) : this.copy(t), n >= 1)
      return this._w = a, this._x = i, this._y = s, this._z = r, this;
    const o = 1 - n * n;
    if (o <= Number.EPSILON) {
      const t2 = 1 - e;
      return this._w = t2 * a + e * this._w, this._x = t2 * i + e * this._x, this._y = t2 * s + e * this._y, this._z = t2 * r + e * this._z, this.normalize(), this;
    }
    const h = Math.sqrt(o), c = Math.atan2(h, n), l = Math.sin((1 - e) * c) / h, u = Math.sin(e * c) / h;
    return this._w = a * l + this._w * u, this._x = i * l + this._x * u, this._y = s * l + this._y * u, this._z = r * l + this._z * u, this._onChangeCallback(), this;
  }
  slerpQuaternions(t, e, i) {
    return this.copy(t).slerp(e, i);
  }
  random() {
    const t = 2 * Math.PI * Math.random(), e = 2 * Math.PI * Math.random(), i = Math.random(), s = Math.sqrt(1 - i), r = Math.sqrt(i);
    return this.set(s * Math.sin(t), s * Math.cos(t), r * Math.sin(e), r * Math.cos(e));
  }
  equals(t) {
    return t._x === this._x && t._y === this._y && t._z === this._z && t._w === this._w;
  }
  fromArray(t, e = 0) {
    return this._x = t[e], this._y = t[e + 1], this._z = t[e + 2], this._w = t[e + 3], this._onChangeCallback(), this;
  }
  toArray(t = [], e = 0) {
    return t[e] = this._x, t[e + 1] = this._y, t[e + 2] = this._z, t[e + 3] = this._w, t;
  }
  fromBufferAttribute(t, e) {
    return this._x = t.getX(e), this._y = t.getY(e), this._z = t.getZ(e), this._w = t.getW(e), this._onChangeCallback(), this;
  }
  toJSON() {
    return this.toArray();
  }
  _onChange(t) {
    return this._onChangeCallback = t, this;
  }
  _onChangeCallback() {
  }
  *[Symbol.iterator]() {
    yield this._x, yield this._y, yield this._z, yield this._w;
  }
}
class Vector3$1 {
  constructor(t = 0, e = 0, i = 0) {
    Vector3$1.prototype.isVector3 = true, this.x = t, this.y = e, this.z = i;
  }
  set(t, e, i) {
    return void 0 === i && (i = this.z), this.x = t, this.y = e, this.z = i, this;
  }
  setScalar(t) {
    return this.x = t, this.y = t, this.z = t, this;
  }
  setX(t) {
    return this.x = t, this;
  }
  setY(t) {
    return this.y = t, this;
  }
  setZ(t) {
    return this.z = t, this;
  }
  setComponent(t, e) {
    switch (t) {
      case 0:
        this.x = e;
        break;
      case 1:
        this.y = e;
        break;
      case 2:
        this.z = e;
        break;
      default:
        throw new Error("index is out of range: " + t);
    }
    return this;
  }
  getComponent(t) {
    switch (t) {
      case 0:
        return this.x;
      case 1:
        return this.y;
      case 2:
        return this.z;
      default:
        throw new Error("index is out of range: " + t);
    }
  }
  clone() {
    return new this.constructor(this.x, this.y, this.z);
  }
  copy(t) {
    return this.x = t.x, this.y = t.y, this.z = t.z, this;
  }
  add(t) {
    return this.x += t.x, this.y += t.y, this.z += t.z, this;
  }
  addScalar(t) {
    return this.x += t, this.y += t, this.z += t, this;
  }
  addVectors(t, e) {
    return this.x = t.x + e.x, this.y = t.y + e.y, this.z = t.z + e.z, this;
  }
  addScaledVector(t, e) {
    return this.x += t.x * e, this.y += t.y * e, this.z += t.z * e, this;
  }
  sub(t) {
    return this.x -= t.x, this.y -= t.y, this.z -= t.z, this;
  }
  subScalar(t) {
    return this.x -= t, this.y -= t, this.z -= t, this;
  }
  subVectors(t, e) {
    return this.x = t.x - e.x, this.y = t.y - e.y, this.z = t.z - e.z, this;
  }
  multiply(t) {
    return this.x *= t.x, this.y *= t.y, this.z *= t.z, this;
  }
  multiplyScalar(t) {
    return this.x *= t, this.y *= t, this.z *= t, this;
  }
  multiplyVectors(t, e) {
    return this.x = t.x * e.x, this.y = t.y * e.y, this.z = t.z * e.z, this;
  }
  applyEuler(t) {
    return this.applyQuaternion(_quaternion$4.setFromEuler(t));
  }
  applyAxisAngle(t, e) {
    return this.applyQuaternion(_quaternion$4.setFromAxisAngle(t, e));
  }
  applyMatrix3(t) {
    const e = this.x, i = this.y, s = this.z, r = t.elements;
    return this.x = r[0] * e + r[3] * i + r[6] * s, this.y = r[1] * e + r[4] * i + r[7] * s, this.z = r[2] * e + r[5] * i + r[8] * s, this;
  }
  applyNormalMatrix(t) {
    return this.applyMatrix3(t).normalize();
  }
  applyMatrix4(t) {
    const e = this.x, i = this.y, s = this.z, r = t.elements, a = 1 / (r[3] * e + r[7] * i + r[11] * s + r[15]);
    return this.x = (r[0] * e + r[4] * i + r[8] * s + r[12]) * a, this.y = (r[1] * e + r[5] * i + r[9] * s + r[13]) * a, this.z = (r[2] * e + r[6] * i + r[10] * s + r[14]) * a, this;
  }
  applyQuaternion(t) {
    const e = this.x, i = this.y, s = this.z, r = t.x, a = t.y, n = t.z, o = t.w, h = 2 * (a * s - n * i), c = 2 * (n * e - r * s), l = 2 * (r * i - a * e);
    return this.x = e + o * h + a * l - n * c, this.y = i + o * c + n * h - r * l, this.z = s + o * l + r * c - a * h, this;
  }
  project(t) {
    return this.applyMatrix4(t.matrixWorldInverse).applyMatrix4(t.projectionMatrix);
  }
  unproject(t) {
    return this.applyMatrix4(t.projectionMatrixInverse).applyMatrix4(t.matrixWorld);
  }
  transformDirection(t) {
    const e = this.x, i = this.y, s = this.z, r = t.elements;
    return this.x = r[0] * e + r[4] * i + r[8] * s, this.y = r[1] * e + r[5] * i + r[9] * s, this.z = r[2] * e + r[6] * i + r[10] * s, this.normalize();
  }
  divide(t) {
    return this.x /= t.x, this.y /= t.y, this.z /= t.z, this;
  }
  divideScalar(t) {
    return this.multiplyScalar(1 / t);
  }
  min(t) {
    return this.x = Math.min(this.x, t.x), this.y = Math.min(this.y, t.y), this.z = Math.min(this.z, t.z), this;
  }
  max(t) {
    return this.x = Math.max(this.x, t.x), this.y = Math.max(this.y, t.y), this.z = Math.max(this.z, t.z), this;
  }
  clamp(t, e) {
    return this.x = clamp$1(this.x, t.x, e.x), this.y = clamp$1(this.y, t.y, e.y), this.z = clamp$1(this.z, t.z, e.z), this;
  }
  clampScalar(t, e) {
    return this.x = clamp$1(this.x, t, e), this.y = clamp$1(this.y, t, e), this.z = clamp$1(this.z, t, e), this;
  }
  clampLength(t, e) {
    const i = this.length();
    return this.divideScalar(i || 1).multiplyScalar(clamp$1(i, t, e));
  }
  floor() {
    return this.x = Math.floor(this.x), this.y = Math.floor(this.y), this.z = Math.floor(this.z), this;
  }
  ceil() {
    return this.x = Math.ceil(this.x), this.y = Math.ceil(this.y), this.z = Math.ceil(this.z), this;
  }
  round() {
    return this.x = Math.round(this.x), this.y = Math.round(this.y), this.z = Math.round(this.z), this;
  }
  roundToZero() {
    return this.x = Math.trunc(this.x), this.y = Math.trunc(this.y), this.z = Math.trunc(this.z), this;
  }
  negate() {
    return this.x = -this.x, this.y = -this.y, this.z = -this.z, this;
  }
  dot(t) {
    return this.x * t.x + this.y * t.y + this.z * t.z;
  }
  lengthSq() {
    return this.x * this.x + this.y * this.y + this.z * this.z;
  }
  length() {
    return Math.sqrt(this.x * this.x + this.y * this.y + this.z * this.z);
  }
  manhattanLength() {
    return Math.abs(this.x) + Math.abs(this.y) + Math.abs(this.z);
  }
  normalize() {
    return this.divideScalar(this.length() || 1);
  }
  setLength(t) {
    return this.normalize().multiplyScalar(t);
  }
  lerp(t, e) {
    return this.x += (t.x - this.x) * e, this.y += (t.y - this.y) * e, this.z += (t.z - this.z) * e, this;
  }
  lerpVectors(t, e, i) {
    return this.x = t.x + (e.x - t.x) * i, this.y = t.y + (e.y - t.y) * i, this.z = t.z + (e.z - t.z) * i, this;
  }
  cross(t) {
    return this.crossVectors(this, t);
  }
  crossVectors(t, e) {
    const i = t.x, s = t.y, r = t.z, a = e.x, n = e.y, o = e.z;
    return this.x = s * o - r * n, this.y = r * a - i * o, this.z = i * n - s * a, this;
  }
  projectOnVector(t) {
    const e = t.lengthSq();
    if (0 === e)
      return this.set(0, 0, 0);
    const i = t.dot(this) / e;
    return this.copy(t).multiplyScalar(i);
  }
  projectOnPlane(t) {
    return _vector$c.copy(this).projectOnVector(t), this.sub(_vector$c);
  }
  reflect(t) {
    return this.sub(_vector$c.copy(t).multiplyScalar(2 * this.dot(t)));
  }
  angleTo(t) {
    const e = Math.sqrt(this.lengthSq() * t.lengthSq());
    if (0 === e)
      return Math.PI / 2;
    const i = this.dot(t) / e;
    return Math.acos(clamp$1(i, -1, 1));
  }
  distanceTo(t) {
    return Math.sqrt(this.distanceToSquared(t));
  }
  distanceToSquared(t) {
    const e = this.x - t.x, i = this.y - t.y, s = this.z - t.z;
    return e * e + i * i + s * s;
  }
  manhattanDistanceTo(t) {
    return Math.abs(this.x - t.x) + Math.abs(this.y - t.y) + Math.abs(this.z - t.z);
  }
  setFromSpherical(t) {
    return this.setFromSphericalCoords(t.radius, t.phi, t.theta);
  }
  setFromSphericalCoords(t, e, i) {
    const s = Math.sin(e) * t;
    return this.x = s * Math.sin(i), this.y = Math.cos(e) * t, this.z = s * Math.cos(i), this;
  }
  setFromCylindrical(t) {
    return this.setFromCylindricalCoords(t.radius, t.theta, t.y);
  }
  setFromCylindricalCoords(t, e, i) {
    return this.x = t * Math.sin(e), this.y = i, this.z = t * Math.cos(e), this;
  }
  setFromMatrixPosition(t) {
    const e = t.elements;
    return this.x = e[12], this.y = e[13], this.z = e[14], this;
  }
  setFromMatrixScale(t) {
    const e = this.setFromMatrixColumn(t, 0).length(), i = this.setFromMatrixColumn(t, 1).length(), s = this.setFromMatrixColumn(t, 2).length();
    return this.x = e, this.y = i, this.z = s, this;
  }
  setFromMatrixColumn(t, e) {
    return this.fromArray(t.elements, 4 * e);
  }
  setFromMatrix3Column(t, e) {
    return this.fromArray(t.elements, 3 * e);
  }
  setFromEuler(t) {
    return this.x = t._x, this.y = t._y, this.z = t._z, this;
  }
  setFromColor(t) {
    return this.x = t.r, this.y = t.g, this.z = t.b, this;
  }
  equals(t) {
    return t.x === this.x && t.y === this.y && t.z === this.z;
  }
  fromArray(t, e = 0) {
    return this.x = t[e], this.y = t[e + 1], this.z = t[e + 2], this;
  }
  toArray(t = [], e = 0) {
    return t[e] = this.x, t[e + 1] = this.y, t[e + 2] = this.z, t;
  }
  fromBufferAttribute(t, e) {
    return this.x = t.getX(e), this.y = t.getY(e), this.z = t.getZ(e), this;
  }
  random() {
    return this.x = Math.random(), this.y = Math.random(), this.z = Math.random(), this;
  }
  randomDirection() {
    const t = Math.random() * Math.PI * 2, e = 2 * Math.random() - 1, i = Math.sqrt(1 - e * e);
    return this.x = i * Math.cos(t), this.y = e, this.z = i * Math.sin(t), this;
  }
  *[Symbol.iterator]() {
    yield this.x, yield this.y, yield this.z;
  }
}
const _vector$c = new Vector3$1(), _quaternion$4 = new Quaternion$1();
class Matrix3 {
  constructor(t, e, i, s, r, a, n, o, h) {
    Matrix3.prototype.isMatrix3 = true, this.elements = [1, 0, 0, 0, 1, 0, 0, 0, 1], void 0 !== t && this.set(t, e, i, s, r, a, n, o, h);
  }
  set(t, e, i, s, r, a, n, o, h) {
    const c = this.elements;
    return c[0] = t, c[1] = s, c[2] = n, c[3] = e, c[4] = r, c[5] = o, c[6] = i, c[7] = a, c[8] = h, this;
  }
  identity() {
    return this.set(1, 0, 0, 0, 1, 0, 0, 0, 1), this;
  }
  copy(t) {
    const e = this.elements, i = t.elements;
    return e[0] = i[0], e[1] = i[1], e[2] = i[2], e[3] = i[3], e[4] = i[4], e[5] = i[5], e[6] = i[6], e[7] = i[7], e[8] = i[8], this;
  }
  extractBasis(t, e, i) {
    return t.setFromMatrix3Column(this, 0), e.setFromMatrix3Column(this, 1), i.setFromMatrix3Column(this, 2), this;
  }
  setFromMatrix4(t) {
    const e = t.elements;
    return this.set(e[0], e[4], e[8], e[1], e[5], e[9], e[2], e[6], e[10]), this;
  }
  multiply(t) {
    return this.multiplyMatrices(this, t);
  }
  premultiply(t) {
    return this.multiplyMatrices(t, this);
  }
  multiplyMatrices(t, e) {
    const i = t.elements, s = e.elements, r = this.elements, a = i[0], n = i[3], o = i[6], h = i[1], c = i[4], l = i[7], u = i[2], d = i[5], m = i[8], _ = s[0], f = s[3], p = s[6], x = s[1], y = s[4], M = s[7], g = s[2], w = s[5], P = s[8];
    return r[0] = a * _ + n * x + o * g, r[3] = a * f + n * y + o * w, r[6] = a * p + n * M + o * P, r[1] = h * _ + c * x + l * g, r[4] = h * f + c * y + l * w, r[7] = h * p + c * M + l * P, r[2] = u * _ + d * x + m * g, r[5] = u * f + d * y + m * w, r[8] = u * p + d * M + m * P, this;
  }
  multiplyScalar(t) {
    const e = this.elements;
    return e[0] *= t, e[3] *= t, e[6] *= t, e[1] *= t, e[4] *= t, e[7] *= t, e[2] *= t, e[5] *= t, e[8] *= t, this;
  }
  determinant() {
    const t = this.elements, e = t[0], i = t[1], s = t[2], r = t[3], a = t[4], n = t[5], o = t[6], h = t[7], c = t[8];
    return e * a * c - e * n * h - i * r * c + i * n * o + s * r * h - s * a * o;
  }
  invert() {
    const t = this.elements, e = t[0], i = t[1], s = t[2], r = t[3], a = t[4], n = t[5], o = t[6], h = t[7], c = t[8], l = c * a - n * h, u = n * o - c * r, d = h * r - a * o, m = e * l + i * u + s * d;
    if (0 === m)
      return this.set(0, 0, 0, 0, 0, 0, 0, 0, 0);
    const _ = 1 / m;
    return t[0] = l * _, t[1] = (s * h - c * i) * _, t[2] = (n * i - s * a) * _, t[3] = u * _, t[4] = (c * e - s * o) * _, t[5] = (s * r - n * e) * _, t[6] = d * _, t[7] = (i * o - h * e) * _, t[8] = (a * e - i * r) * _, this;
  }
  transpose() {
    let t;
    const e = this.elements;
    return t = e[1], e[1] = e[3], e[3] = t, t = e[2], e[2] = e[6], e[6] = t, t = e[5], e[5] = e[7], e[7] = t, this;
  }
  getNormalMatrix(t) {
    return this.setFromMatrix4(t).invert().transpose();
  }
  transposeIntoArray(t) {
    const e = this.elements;
    return t[0] = e[0], t[1] = e[3], t[2] = e[6], t[3] = e[1], t[4] = e[4], t[5] = e[7], t[6] = e[2], t[7] = e[5], t[8] = e[8], this;
  }
  setUvTransform(t, e, i, s, r, a, n) {
    const o = Math.cos(r), h = Math.sin(r);
    return this.set(i * o, i * h, -i * (o * a + h * n) + a + t, -s * h, s * o, -s * (-h * a + o * n) + n + e, 0, 0, 1), this;
  }
  scale(t, e) {
    return this.premultiply(_m3.makeScale(t, e)), this;
  }
  rotate(t) {
    return this.premultiply(_m3.makeRotation(-t)), this;
  }
  translate(t, e) {
    return this.premultiply(_m3.makeTranslation(t, e)), this;
  }
  makeTranslation(t, e) {
    return t.isVector2 ? this.set(1, 0, t.x, 0, 1, t.y, 0, 0, 1) : this.set(1, 0, t, 0, 1, e, 0, 0, 1), this;
  }
  makeRotation(t) {
    const e = Math.cos(t), i = Math.sin(t);
    return this.set(e, -i, 0, i, e, 0, 0, 0, 1), this;
  }
  makeScale(t, e) {
    return this.set(t, 0, 0, 0, e, 0, 0, 0, 1), this;
  }
  equals(t) {
    const e = this.elements, i = t.elements;
    for (let t2 = 0; t2 < 9; t2++)
      if (e[t2] !== i[t2])
        return false;
    return true;
  }
  fromArray(t, e = 0) {
    for (let i = 0; i < 9; i++)
      this.elements[i] = t[i + e];
    return this;
  }
  toArray(t = [], e = 0) {
    const i = this.elements;
    return t[e] = i[0], t[e + 1] = i[1], t[e + 2] = i[2], t[e + 3] = i[3], t[e + 4] = i[4], t[e + 5] = i[5], t[e + 6] = i[6], t[e + 7] = i[7], t[e + 8] = i[8], t;
  }
  clone() {
    return new this.constructor().fromArray(this.elements);
  }
}
const _m3 = new Matrix3();
class Vector4 {
  constructor(t = 0, e = 0, i = 0, s = 1) {
    Vector4.prototype.isVector4 = true, this.x = t, this.y = e, this.z = i, this.w = s;
  }
  get width() {
    return this.z;
  }
  set width(t) {
    this.z = t;
  }
  get height() {
    return this.w;
  }
  set height(t) {
    this.w = t;
  }
  set(t, e, i, s) {
    return this.x = t, this.y = e, this.z = i, this.w = s, this;
  }
  setScalar(t) {
    return this.x = t, this.y = t, this.z = t, this.w = t, this;
  }
  setX(t) {
    return this.x = t, this;
  }
  setY(t) {
    return this.y = t, this;
  }
  setZ(t) {
    return this.z = t, this;
  }
  setW(t) {
    return this.w = t, this;
  }
  setComponent(t, e) {
    switch (t) {
      case 0:
        this.x = e;
        break;
      case 1:
        this.y = e;
        break;
      case 2:
        this.z = e;
        break;
      case 3:
        this.w = e;
        break;
      default:
        throw new Error("index is out of range: " + t);
    }
    return this;
  }
  getComponent(t) {
    switch (t) {
      case 0:
        return this.x;
      case 1:
        return this.y;
      case 2:
        return this.z;
      case 3:
        return this.w;
      default:
        throw new Error("index is out of range: " + t);
    }
  }
  clone() {
    return new this.constructor(this.x, this.y, this.z, this.w);
  }
  copy(t) {
    return this.x = t.x, this.y = t.y, this.z = t.z, this.w = void 0 !== t.w ? t.w : 1, this;
  }
  add(t) {
    return this.x += t.x, this.y += t.y, this.z += t.z, this.w += t.w, this;
  }
  addScalar(t) {
    return this.x += t, this.y += t, this.z += t, this.w += t, this;
  }
  addVectors(t, e) {
    return this.x = t.x + e.x, this.y = t.y + e.y, this.z = t.z + e.z, this.w = t.w + e.w, this;
  }
  addScaledVector(t, e) {
    return this.x += t.x * e, this.y += t.y * e, this.z += t.z * e, this.w += t.w * e, this;
  }
  sub(t) {
    return this.x -= t.x, this.y -= t.y, this.z -= t.z, this.w -= t.w, this;
  }
  subScalar(t) {
    return this.x -= t, this.y -= t, this.z -= t, this.w -= t, this;
  }
  subVectors(t, e) {
    return this.x = t.x - e.x, this.y = t.y - e.y, this.z = t.z - e.z, this.w = t.w - e.w, this;
  }
  multiply(t) {
    return this.x *= t.x, this.y *= t.y, this.z *= t.z, this.w *= t.w, this;
  }
  multiplyScalar(t) {
    return this.x *= t, this.y *= t, this.z *= t, this.w *= t, this;
  }
  applyMatrix4(t) {
    const e = this.x, i = this.y, s = this.z, r = this.w, a = t.elements;
    return this.x = a[0] * e + a[4] * i + a[8] * s + a[12] * r, this.y = a[1] * e + a[5] * i + a[9] * s + a[13] * r, this.z = a[2] * e + a[6] * i + a[10] * s + a[14] * r, this.w = a[3] * e + a[7] * i + a[11] * s + a[15] * r, this;
  }
  divide(t) {
    return this.x /= t.x, this.y /= t.y, this.z /= t.z, this.w /= t.w, this;
  }
  divideScalar(t) {
    return this.multiplyScalar(1 / t);
  }
  setAxisAngleFromQuaternion(t) {
    this.w = 2 * Math.acos(t.w);
    const e = Math.sqrt(1 - t.w * t.w);
    return e < 1e-4 ? (this.x = 1, this.y = 0, this.z = 0) : (this.x = t.x / e, this.y = t.y / e, this.z = t.z / e), this;
  }
  setAxisAngleFromRotationMatrix(t) {
    let e, i, s, r;
    const a = 0.01, n = 0.1, o = t.elements, h = o[0], c = o[4], l = o[8], u = o[1], d = o[5], m = o[9], _ = o[2], f = o[6], p = o[10];
    if (Math.abs(c - u) < a && Math.abs(l - _) < a && Math.abs(m - f) < a) {
      if (Math.abs(c + u) < n && Math.abs(l + _) < n && Math.abs(m + f) < n && Math.abs(h + d + p - 3) < n)
        return this.set(1, 0, 0, 0), this;
      e = Math.PI;
      const t2 = (h + 1) / 2, o2 = (d + 1) / 2, x2 = (p + 1) / 2, y = (c + u) / 4, M = (l + _) / 4, g = (m + f) / 4;
      return t2 > o2 && t2 > x2 ? t2 < a ? (i = 0, s = 0.707106781, r = 0.707106781) : (i = Math.sqrt(t2), s = y / i, r = M / i) : o2 > x2 ? o2 < a ? (i = 0.707106781, s = 0, r = 0.707106781) : (s = Math.sqrt(o2), i = y / s, r = g / s) : x2 < a ? (i = 0.707106781, s = 0.707106781, r = 0) : (r = Math.sqrt(x2), i = M / r, s = g / r), this.set(i, s, r, e), this;
    }
    let x = Math.sqrt((f - m) * (f - m) + (l - _) * (l - _) + (u - c) * (u - c));
    return Math.abs(x) < 1e-3 && (x = 1), this.x = (f - m) / x, this.y = (l - _) / x, this.z = (u - c) / x, this.w = Math.acos((h + d + p - 1) / 2), this;
  }
  setFromMatrixPosition(t) {
    const e = t.elements;
    return this.x = e[12], this.y = e[13], this.z = e[14], this.w = e[15], this;
  }
  min(t) {
    return this.x = Math.min(this.x, t.x), this.y = Math.min(this.y, t.y), this.z = Math.min(this.z, t.z), this.w = Math.min(this.w, t.w), this;
  }
  max(t) {
    return this.x = Math.max(this.x, t.x), this.y = Math.max(this.y, t.y), this.z = Math.max(this.z, t.z), this.w = Math.max(this.w, t.w), this;
  }
  clamp(t, e) {
    return this.x = clamp$1(this.x, t.x, e.x), this.y = clamp$1(this.y, t.y, e.y), this.z = clamp$1(this.z, t.z, e.z), this.w = clamp$1(this.w, t.w, e.w), this;
  }
  clampScalar(t, e) {
    return this.x = clamp$1(this.x, t, e), this.y = clamp$1(this.y, t, e), this.z = clamp$1(this.z, t, e), this.w = clamp$1(this.w, t, e), this;
  }
  clampLength(t, e) {
    const i = this.length();
    return this.divideScalar(i || 1).multiplyScalar(clamp$1(i, t, e));
  }
  floor() {
    return this.x = Math.floor(this.x), this.y = Math.floor(this.y), this.z = Math.floor(this.z), this.w = Math.floor(this.w), this;
  }
  ceil() {
    return this.x = Math.ceil(this.x), this.y = Math.ceil(this.y), this.z = Math.ceil(this.z), this.w = Math.ceil(this.w), this;
  }
  round() {
    return this.x = Math.round(this.x), this.y = Math.round(this.y), this.z = Math.round(this.z), this.w = Math.round(this.w), this;
  }
  roundToZero() {
    return this.x = Math.trunc(this.x), this.y = Math.trunc(this.y), this.z = Math.trunc(this.z), this.w = Math.trunc(this.w), this;
  }
  negate() {
    return this.x = -this.x, this.y = -this.y, this.z = -this.z, this.w = -this.w, this;
  }
  dot(t) {
    return this.x * t.x + this.y * t.y + this.z * t.z + this.w * t.w;
  }
  lengthSq() {
    return this.x * this.x + this.y * this.y + this.z * this.z + this.w * this.w;
  }
  length() {
    return Math.sqrt(this.x * this.x + this.y * this.y + this.z * this.z + this.w * this.w);
  }
  manhattanLength() {
    return Math.abs(this.x) + Math.abs(this.y) + Math.abs(this.z) + Math.abs(this.w);
  }
  normalize() {
    return this.divideScalar(this.length() || 1);
  }
  setLength(t) {
    return this.normalize().multiplyScalar(t);
  }
  lerp(t, e) {
    return this.x += (t.x - this.x) * e, this.y += (t.y - this.y) * e, this.z += (t.z - this.z) * e, this.w += (t.w - this.w) * e, this;
  }
  lerpVectors(t, e, i) {
    return this.x = t.x + (e.x - t.x) * i, this.y = t.y + (e.y - t.y) * i, this.z = t.z + (e.z - t.z) * i, this.w = t.w + (e.w - t.w) * i, this;
  }
  equals(t) {
    return t.x === this.x && t.y === this.y && t.z === this.z && t.w === this.w;
  }
  fromArray(t, e = 0) {
    return this.x = t[e], this.y = t[e + 1], this.z = t[e + 2], this.w = t[e + 3], this;
  }
  toArray(t = [], e = 0) {
    return t[e] = this.x, t[e + 1] = this.y, t[e + 2] = this.z, t[e + 3] = this.w, t;
  }
  fromBufferAttribute(t, e) {
    return this.x = t.getX(e), this.y = t.getY(e), this.z = t.getZ(e), this.w = t.getW(e), this;
  }
  random() {
    return this.x = Math.random(), this.y = Math.random(), this.z = Math.random(), this.w = Math.random(), this;
  }
  *[Symbol.iterator]() {
    yield this.x, yield this.y, yield this.z, yield this.w;
  }
}
class Box3 {
  constructor(t = new Vector3$1(1 / 0, 1 / 0, 1 / 0), e = new Vector3$1(-1 / 0, -1 / 0, -1 / 0)) {
    this.isBox3 = true, this.min = t, this.max = e;
  }
  set(t, e) {
    return this.min.copy(t), this.max.copy(e), this;
  }
  setFromArray(t) {
    this.makeEmpty();
    for (let e = 0, i = t.length; e < i; e += 3)
      this.expandByPoint(_vector$b.fromArray(t, e));
    return this;
  }
  setFromBufferAttribute(t) {
    this.makeEmpty();
    for (let e = 0, i = t.count; e < i; e++)
      this.expandByPoint(_vector$b.fromBufferAttribute(t, e));
    return this;
  }
  setFromPoints(t) {
    this.makeEmpty();
    for (let e = 0, i = t.length; e < i; e++)
      this.expandByPoint(t[e]);
    return this;
  }
  setFromCenterAndSize(t, e) {
    const i = _vector$b.copy(e).multiplyScalar(0.5);
    return this.min.copy(t).sub(i), this.max.copy(t).add(i), this;
  }
  setFromObject(t, e = false) {
    return this.makeEmpty(), this.expandByObject(t, e);
  }
  clone() {
    return new this.constructor().copy(this);
  }
  copy(t) {
    return this.min.copy(t.min), this.max.copy(t.max), this;
  }
  makeEmpty() {
    return this.min.x = this.min.y = this.min.z = 1 / 0, this.max.x = this.max.y = this.max.z = -1 / 0, this;
  }
  isEmpty() {
    return this.max.x < this.min.x || this.max.y < this.min.y || this.max.z < this.min.z;
  }
  getCenter(t) {
    return this.isEmpty() ? t.set(0, 0, 0) : t.addVectors(this.min, this.max).multiplyScalar(0.5);
  }
  getSize(t) {
    return this.isEmpty() ? t.set(0, 0, 0) : t.subVectors(this.max, this.min);
  }
  expandByPoint(t) {
    return this.min.min(t), this.max.max(t), this;
  }
  expandByVector(t) {
    return this.min.sub(t), this.max.add(t), this;
  }
  expandByScalar(t) {
    return this.min.addScalar(-t), this.max.addScalar(t), this;
  }
  expandByObject(t, e = false) {
    t.updateWorldMatrix(false, false);
    const i = t.geometry;
    if (void 0 !== i) {
      const s2 = i.getAttribute("position");
      if (true === e && void 0 !== s2 && true !== t.isInstancedMesh)
        for (let e2 = 0, i2 = s2.count; e2 < i2; e2++)
          true === t.isMesh ? t.getVertexPosition(e2, _vector$b) : _vector$b.fromBufferAttribute(s2, e2), _vector$b.applyMatrix4(t.matrixWorld), this.expandByPoint(_vector$b);
      else
        void 0 !== t.boundingBox ? (null === t.boundingBox && t.computeBoundingBox(), _box$4.copy(t.boundingBox)) : (null === i.boundingBox && i.computeBoundingBox(), _box$4.copy(i.boundingBox)), _box$4.applyMatrix4(t.matrixWorld), this.union(_box$4);
    }
    const s = t.children;
    for (let t2 = 0, i2 = s.length; t2 < i2; t2++)
      this.expandByObject(s[t2], e);
    return this;
  }
  containsPoint(t) {
    return t.x >= this.min.x && t.x <= this.max.x && t.y >= this.min.y && t.y <= this.max.y && t.z >= this.min.z && t.z <= this.max.z;
  }
  containsBox(t) {
    return this.min.x <= t.min.x && t.max.x <= this.max.x && this.min.y <= t.min.y && t.max.y <= this.max.y && this.min.z <= t.min.z && t.max.z <= this.max.z;
  }
  getParameter(t, e) {
    return e.set((t.x - this.min.x) / (this.max.x - this.min.x), (t.y - this.min.y) / (this.max.y - this.min.y), (t.z - this.min.z) / (this.max.z - this.min.z));
  }
  intersectsBox(t) {
    return t.max.x >= this.min.x && t.min.x <= this.max.x && t.max.y >= this.min.y && t.min.y <= this.max.y && t.max.z >= this.min.z && t.min.z <= this.max.z;
  }
  intersectsSphere(t) {
    return this.clampPoint(t.center, _vector$b), _vector$b.distanceToSquared(t.center) <= t.radius * t.radius;
  }
  intersectsPlane(t) {
    let e, i;
    return t.normal.x > 0 ? (e = t.normal.x * this.min.x, i = t.normal.x * this.max.x) : (e = t.normal.x * this.max.x, i = t.normal.x * this.min.x), t.normal.y > 0 ? (e += t.normal.y * this.min.y, i += t.normal.y * this.max.y) : (e += t.normal.y * this.max.y, i += t.normal.y * this.min.y), t.normal.z > 0 ? (e += t.normal.z * this.min.z, i += t.normal.z * this.max.z) : (e += t.normal.z * this.max.z, i += t.normal.z * this.min.z), e <= -t.constant && i >= -t.constant;
  }
  intersectsTriangle(t) {
    if (this.isEmpty())
      return false;
    this.getCenter(_center), _extents.subVectors(this.max, _center), _v0$2.subVectors(t.a, _center), _v1$7.subVectors(t.b, _center), _v2$4.subVectors(t.c, _center), _f0.subVectors(_v1$7, _v0$2), _f1.subVectors(_v2$4, _v1$7), _f2.subVectors(_v0$2, _v2$4);
    let e = [0, -_f0.z, _f0.y, 0, -_f1.z, _f1.y, 0, -_f2.z, _f2.y, _f0.z, 0, -_f0.x, _f1.z, 0, -_f1.x, _f2.z, 0, -_f2.x, -_f0.y, _f0.x, 0, -_f1.y, _f1.x, 0, -_f2.y, _f2.x, 0];
    return !!satForAxes(e, _v0$2, _v1$7, _v2$4, _extents) && (e = [1, 0, 0, 0, 1, 0, 0, 0, 1], !!satForAxes(e, _v0$2, _v1$7, _v2$4, _extents) && (_triangleNormal.crossVectors(_f0, _f1), e = [_triangleNormal.x, _triangleNormal.y, _triangleNormal.z], satForAxes(e, _v0$2, _v1$7, _v2$4, _extents)));
  }
  clampPoint(t, e) {
    return e.copy(t).clamp(this.min, this.max);
  }
  distanceToPoint(t) {
    return this.clampPoint(t, _vector$b).distanceTo(t);
  }
  getBoundingSphere(t) {
    return this.isEmpty() ? t.makeEmpty() : (this.getCenter(t.center), t.radius = 0.5 * this.getSize(_vector$b).length()), t;
  }
  intersect(t) {
    return this.min.max(t.min), this.max.min(t.max), this.isEmpty() && this.makeEmpty(), this;
  }
  union(t) {
    return this.min.min(t.min), this.max.max(t.max), this;
  }
  applyMatrix4(t) {
    return this.isEmpty() || (_points[0].set(this.min.x, this.min.y, this.min.z).applyMatrix4(t), _points[1].set(this.min.x, this.min.y, this.max.z).applyMatrix4(t), _points[2].set(this.min.x, this.max.y, this.min.z).applyMatrix4(t), _points[3].set(this.min.x, this.max.y, this.max.z).applyMatrix4(t), _points[4].set(this.max.x, this.min.y, this.min.z).applyMatrix4(t), _points[5].set(this.max.x, this.min.y, this.max.z).applyMatrix4(t), _points[6].set(this.max.x, this.max.y, this.min.z).applyMatrix4(t), _points[7].set(this.max.x, this.max.y, this.max.z).applyMatrix4(t), this.setFromPoints(_points)), this;
  }
  translate(t) {
    return this.min.add(t), this.max.add(t), this;
  }
  equals(t) {
    return t.min.equals(this.min) && t.max.equals(this.max);
  }
  toJSON() {
    return { min: this.min.toArray(), max: this.max.toArray() };
  }
  fromJSON(t) {
    return this.min.fromArray(t.min), this.max.fromArray(t.max), this;
  }
}
const _points = [new Vector3$1(), new Vector3$1(), new Vector3$1(), new Vector3$1(), new Vector3$1(), new Vector3$1(), new Vector3$1(), new Vector3$1()], _vector$b = new Vector3$1(), _box$4 = new Box3(), _v0$2 = new Vector3$1(), _v1$7 = new Vector3$1(), _v2$4 = new Vector3$1(), _f0 = new Vector3$1(), _f1 = new Vector3$1(), _f2 = new Vector3$1(), _center = new Vector3$1(), _extents = new Vector3$1(), _triangleNormal = new Vector3$1(), _testAxis = new Vector3$1();
function satForAxes(t, e, i, s, r) {
  for (let a = 0, n = t.length - 3; a <= n; a += 3) {
    _testAxis.fromArray(t, a);
    const n2 = r.x * Math.abs(_testAxis.x) + r.y * Math.abs(_testAxis.y) + r.z * Math.abs(_testAxis.z), o = e.dot(_testAxis), h = i.dot(_testAxis), c = s.dot(_testAxis);
    if (Math.max(-Math.max(o, h, c), Math.min(o, h, c)) > n2)
      return false;
  }
  return true;
}
const _vector$a = new Vector3$1(), _segCenter = new Vector3$1(), _segDir = new Vector3$1(), _diff = new Vector3$1(), _edge1 = new Vector3$1(), _edge2 = new Vector3$1(), _normal$1 = new Vector3$1();
class Ray {
  constructor(t = new Vector3$1(), e = new Vector3$1(0, 0, -1)) {
    this.origin = t, this.direction = e;
  }
  set(t, e) {
    return this.origin.copy(t), this.direction.copy(e), this;
  }
  copy(t) {
    return this.origin.copy(t.origin), this.direction.copy(t.direction), this;
  }
  at(t, e) {
    return e.copy(this.origin).addScaledVector(this.direction, t);
  }
  lookAt(t) {
    return this.direction.copy(t).sub(this.origin).normalize(), this;
  }
  recast(t) {
    return this.origin.copy(this.at(t, _vector$a)), this;
  }
  closestPointToPoint(t, e) {
    e.subVectors(t, this.origin);
    const i = e.dot(this.direction);
    return i < 0 ? e.copy(this.origin) : e.copy(this.origin).addScaledVector(this.direction, i);
  }
  distanceToPoint(t) {
    return Math.sqrt(this.distanceSqToPoint(t));
  }
  distanceSqToPoint(t) {
    const e = _vector$a.subVectors(t, this.origin).dot(this.direction);
    return e < 0 ? this.origin.distanceToSquared(t) : (_vector$a.copy(this.origin).addScaledVector(this.direction, e), _vector$a.distanceToSquared(t));
  }
  distanceSqToSegment(t, e, i, s) {
    _segCenter.copy(t).add(e).multiplyScalar(0.5), _segDir.copy(e).sub(t).normalize(), _diff.copy(this.origin).sub(_segCenter);
    const r = 0.5 * t.distanceTo(e), a = -this.direction.dot(_segDir), n = _diff.dot(this.direction), o = -_diff.dot(_segDir), h = _diff.lengthSq(), c = Math.abs(1 - a * a);
    let l, u, d, m;
    if (c > 0)
      if (l = a * o - n, u = a * n - o, m = r * c, l >= 0)
        if (u >= -m)
          if (u <= m) {
            const t2 = 1 / c;
            l *= t2, u *= t2, d = l * (l + a * u + 2 * n) + u * (a * l + u + 2 * o) + h;
          } else
            u = r, l = Math.max(0, -(a * u + n)), d = -l * l + u * (u + 2 * o) + h;
        else
          u = -r, l = Math.max(0, -(a * u + n)), d = -l * l + u * (u + 2 * o) + h;
      else
        u <= -m ? (l = Math.max(0, -(-a * r + n)), u = l > 0 ? -r : Math.min(Math.max(-r, -o), r), d = -l * l + u * (u + 2 * o) + h) : u <= m ? (l = 0, u = Math.min(Math.max(-r, -o), r), d = u * (u + 2 * o) + h) : (l = Math.max(0, -(a * r + n)), u = l > 0 ? r : Math.min(Math.max(-r, -o), r), d = -l * l + u * (u + 2 * o) + h);
    else
      u = a > 0 ? -r : r, l = Math.max(0, -(a * u + n)), d = -l * l + u * (u + 2 * o) + h;
    return i && i.copy(this.origin).addScaledVector(this.direction, l), s && s.copy(_segCenter).addScaledVector(_segDir, u), d;
  }
  intersectSphere(t, e) {
    _vector$a.subVectors(t.center, this.origin);
    const i = _vector$a.dot(this.direction), s = _vector$a.dot(_vector$a) - i * i, r = t.radius * t.radius;
    if (s > r)
      return null;
    const a = Math.sqrt(r - s), n = i - a, o = i + a;
    return o < 0 ? null : n < 0 ? this.at(o, e) : this.at(n, e);
  }
  intersectsSphere(t) {
    return !(t.radius < 0) && this.distanceSqToPoint(t.center) <= t.radius * t.radius;
  }
  distanceToPlane(t) {
    const e = t.normal.dot(this.direction);
    if (0 === e)
      return 0 === t.distanceToPoint(this.origin) ? 0 : null;
    const i = -(this.origin.dot(t.normal) + t.constant) / e;
    return i >= 0 ? i : null;
  }
  intersectPlane(t, e) {
    const i = this.distanceToPlane(t);
    return null === i ? null : this.at(i, e);
  }
  intersectsPlane(t) {
    const e = t.distanceToPoint(this.origin);
    if (0 === e)
      return true;
    return t.normal.dot(this.direction) * e < 0;
  }
  intersectBox(t, e) {
    let i, s, r, a, n, o;
    const h = 1 / this.direction.x, c = 1 / this.direction.y, l = 1 / this.direction.z, u = this.origin;
    return h >= 0 ? (i = (t.min.x - u.x) * h, s = (t.max.x - u.x) * h) : (i = (t.max.x - u.x) * h, s = (t.min.x - u.x) * h), c >= 0 ? (r = (t.min.y - u.y) * c, a = (t.max.y - u.y) * c) : (r = (t.max.y - u.y) * c, a = (t.min.y - u.y) * c), i > a || r > s ? null : ((r > i || isNaN(i)) && (i = r), (a < s || isNaN(s)) && (s = a), l >= 0 ? (n = (t.min.z - u.z) * l, o = (t.max.z - u.z) * l) : (n = (t.max.z - u.z) * l, o = (t.min.z - u.z) * l), i > o || n > s ? null : ((n > i || i != i) && (i = n), (o < s || s != s) && (s = o), s < 0 ? null : this.at(i >= 0 ? i : s, e)));
  }
  intersectsBox(t) {
    return null !== this.intersectBox(t, _vector$a);
  }
  intersectTriangle(t, e, i, s, r) {
    _edge1.subVectors(e, t), _edge2.subVectors(i, t), _normal$1.crossVectors(_edge1, _edge2);
    let a, n = this.direction.dot(_normal$1);
    if (n > 0) {
      if (s)
        return null;
      a = 1;
    } else {
      if (!(n < 0))
        return null;
      a = -1, n = -n;
    }
    _diff.subVectors(this.origin, t);
    const o = a * this.direction.dot(_edge2.crossVectors(_diff, _edge2));
    if (o < 0)
      return null;
    const h = a * this.direction.dot(_edge1.cross(_diff));
    if (h < 0)
      return null;
    if (o + h > n)
      return null;
    const c = -a * _diff.dot(_normal$1);
    return c < 0 ? null : this.at(c / n, r);
  }
  applyMatrix4(t) {
    return this.origin.applyMatrix4(t), this.direction.transformDirection(t), this;
  }
  equals(t) {
    return t.origin.equals(this.origin) && t.direction.equals(this.direction);
  }
  clone() {
    return new this.constructor().copy(this);
  }
}
class Matrix4 {
  constructor(t, e, i, s, r, a, n, o, h, c, l, u, d, m, _, f) {
    Matrix4.prototype.isMatrix4 = true, this.elements = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1], void 0 !== t && this.set(t, e, i, s, r, a, n, o, h, c, l, u, d, m, _, f);
  }
  set(t, e, i, s, r, a, n, o, h, c, l, u, d, m, _, f) {
    const p = this.elements;
    return p[0] = t, p[4] = e, p[8] = i, p[12] = s, p[1] = r, p[5] = a, p[9] = n, p[13] = o, p[2] = h, p[6] = c, p[10] = l, p[14] = u, p[3] = d, p[7] = m, p[11] = _, p[15] = f, this;
  }
  identity() {
    return this.set(1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1), this;
  }
  clone() {
    return new Matrix4().fromArray(this.elements);
  }
  copy(t) {
    const e = this.elements, i = t.elements;
    return e[0] = i[0], e[1] = i[1], e[2] = i[2], e[3] = i[3], e[4] = i[4], e[5] = i[5], e[6] = i[6], e[7] = i[7], e[8] = i[8], e[9] = i[9], e[10] = i[10], e[11] = i[11], e[12] = i[12], e[13] = i[13], e[14] = i[14], e[15] = i[15], this;
  }
  copyPosition(t) {
    const e = this.elements, i = t.elements;
    return e[12] = i[12], e[13] = i[13], e[14] = i[14], this;
  }
  setFromMatrix3(t) {
    const e = t.elements;
    return this.set(e[0], e[3], e[6], 0, e[1], e[4], e[7], 0, e[2], e[5], e[8], 0, 0, 0, 0, 1), this;
  }
  extractBasis(t, e, i) {
    return t.setFromMatrixColumn(this, 0), e.setFromMatrixColumn(this, 1), i.setFromMatrixColumn(this, 2), this;
  }
  makeBasis(t, e, i) {
    return this.set(t.x, e.x, i.x, 0, t.y, e.y, i.y, 0, t.z, e.z, i.z, 0, 0, 0, 0, 1), this;
  }
  extractRotation(t) {
    const e = this.elements, i = t.elements, s = 1 / _v1$5.setFromMatrixColumn(t, 0).length(), r = 1 / _v1$5.setFromMatrixColumn(t, 1).length(), a = 1 / _v1$5.setFromMatrixColumn(t, 2).length();
    return e[0] = i[0] * s, e[1] = i[1] * s, e[2] = i[2] * s, e[3] = 0, e[4] = i[4] * r, e[5] = i[5] * r, e[6] = i[6] * r, e[7] = 0, e[8] = i[8] * a, e[9] = i[9] * a, e[10] = i[10] * a, e[11] = 0, e[12] = 0, e[13] = 0, e[14] = 0, e[15] = 1, this;
  }
  makeRotationFromEuler(t) {
    const e = this.elements, i = t.x, s = t.y, r = t.z, a = Math.cos(i), n = Math.sin(i), o = Math.cos(s), h = Math.sin(s), c = Math.cos(r), l = Math.sin(r);
    if ("XYZ" === t.order) {
      const t2 = a * c, i2 = a * l, s2 = n * c, r2 = n * l;
      e[0] = o * c, e[4] = -o * l, e[8] = h, e[1] = i2 + s2 * h, e[5] = t2 - r2 * h, e[9] = -n * o, e[2] = r2 - t2 * h, e[6] = s2 + i2 * h, e[10] = a * o;
    } else if ("YXZ" === t.order) {
      const t2 = o * c, i2 = o * l, s2 = h * c, r2 = h * l;
      e[0] = t2 + r2 * n, e[4] = s2 * n - i2, e[8] = a * h, e[1] = a * l, e[5] = a * c, e[9] = -n, e[2] = i2 * n - s2, e[6] = r2 + t2 * n, e[10] = a * o;
    } else if ("ZXY" === t.order) {
      const t2 = o * c, i2 = o * l, s2 = h * c, r2 = h * l;
      e[0] = t2 - r2 * n, e[4] = -a * l, e[8] = s2 + i2 * n, e[1] = i2 + s2 * n, e[5] = a * c, e[9] = r2 - t2 * n, e[2] = -a * h, e[6] = n, e[10] = a * o;
    } else if ("ZYX" === t.order) {
      const t2 = a * c, i2 = a * l, s2 = n * c, r2 = n * l;
      e[0] = o * c, e[4] = s2 * h - i2, e[8] = t2 * h + r2, e[1] = o * l, e[5] = r2 * h + t2, e[9] = i2 * h - s2, e[2] = -h, e[6] = n * o, e[10] = a * o;
    } else if ("YZX" === t.order) {
      const t2 = a * o, i2 = a * h, s2 = n * o, r2 = n * h;
      e[0] = o * c, e[4] = r2 - t2 * l, e[8] = s2 * l + i2, e[1] = l, e[5] = a * c, e[9] = -n * c, e[2] = -h * c, e[6] = i2 * l + s2, e[10] = t2 - r2 * l;
    } else if ("XZY" === t.order) {
      const t2 = a * o, i2 = a * h, s2 = n * o, r2 = n * h;
      e[0] = o * c, e[4] = -l, e[8] = h * c, e[1] = t2 * l + r2, e[5] = a * c, e[9] = i2 * l - s2, e[2] = s2 * l - i2, e[6] = n * c, e[10] = r2 * l + t2;
    }
    return e[3] = 0, e[7] = 0, e[11] = 0, e[12] = 0, e[13] = 0, e[14] = 0, e[15] = 1, this;
  }
  makeRotationFromQuaternion(t) {
    return this.compose(_zero, t, _one);
  }
  lookAt(t, e, i) {
    const s = this.elements;
    return _z.subVectors(t, e), 0 === _z.lengthSq() && (_z.z = 1), _z.normalize(), _x.crossVectors(i, _z), 0 === _x.lengthSq() && (1 === Math.abs(i.z) ? _z.x += 1e-4 : _z.z += 1e-4, _z.normalize(), _x.crossVectors(i, _z)), _x.normalize(), _y.crossVectors(_z, _x), s[0] = _x.x, s[4] = _y.x, s[8] = _z.x, s[1] = _x.y, s[5] = _y.y, s[9] = _z.y, s[2] = _x.z, s[6] = _y.z, s[10] = _z.z, this;
  }
  multiply(t) {
    return this.multiplyMatrices(this, t);
  }
  premultiply(t) {
    return this.multiplyMatrices(t, this);
  }
  multiplyMatrices(t, e) {
    const i = t.elements, s = e.elements, r = this.elements, a = i[0], n = i[4], o = i[8], h = i[12], c = i[1], l = i[5], u = i[9], d = i[13], m = i[2], _ = i[6], f = i[10], p = i[14], x = i[3], y = i[7], M = i[11], g = i[15], w = s[0], P = s[4], S = s[8], C = s[12], E = s[1], v = s[5], b = s[9], z = s[13], T = s[2], A2 = s[6], O = s[10], G = s[14], N = s[3], I = s[7], R = s[11], $ = s[15];
    return r[0] = a * w + n * E + o * T + h * N, r[4] = a * P + n * v + o * A2 + h * I, r[8] = a * S + n * b + o * O + h * R, r[12] = a * C + n * z + o * G + h * $, r[1] = c * w + l * E + u * T + d * N, r[5] = c * P + l * v + u * A2 + d * I, r[9] = c * S + l * b + u * O + d * R, r[13] = c * C + l * z + u * G + d * $, r[2] = m * w + _ * E + f * T + p * N, r[6] = m * P + _ * v + f * A2 + p * I, r[10] = m * S + _ * b + f * O + p * R, r[14] = m * C + _ * z + f * G + p * $, r[3] = x * w + y * E + M * T + g * N, r[7] = x * P + y * v + M * A2 + g * I, r[11] = x * S + y * b + M * O + g * R, r[15] = x * C + y * z + M * G + g * $, this;
  }
  multiplyScalar(t) {
    const e = this.elements;
    return e[0] *= t, e[4] *= t, e[8] *= t, e[12] *= t, e[1] *= t, e[5] *= t, e[9] *= t, e[13] *= t, e[2] *= t, e[6] *= t, e[10] *= t, e[14] *= t, e[3] *= t, e[7] *= t, e[11] *= t, e[15] *= t, this;
  }
  determinant() {
    const t = this.elements, e = t[0], i = t[4], s = t[8], r = t[12], a = t[1], n = t[5], o = t[9], h = t[13], c = t[2], l = t[6], u = t[10], d = t[14];
    return t[3] * (+r * o * l - s * h * l - r * n * u + i * h * u + s * n * d - i * o * d) + t[7] * (+e * o * d - e * h * u + r * a * u - s * a * d + s * h * c - r * o * c) + t[11] * (+e * h * l - e * n * d - r * a * l + i * a * d + r * n * c - i * h * c) + t[15] * (-s * n * c - e * o * l + e * n * u + s * a * l - i * a * u + i * o * c);
  }
  transpose() {
    const t = this.elements;
    let e;
    return e = t[1], t[1] = t[4], t[4] = e, e = t[2], t[2] = t[8], t[8] = e, e = t[6], t[6] = t[9], t[9] = e, e = t[3], t[3] = t[12], t[12] = e, e = t[7], t[7] = t[13], t[13] = e, e = t[11], t[11] = t[14], t[14] = e, this;
  }
  setPosition(t, e, i) {
    const s = this.elements;
    return t.isVector3 ? (s[12] = t.x, s[13] = t.y, s[14] = t.z) : (s[12] = t, s[13] = e, s[14] = i), this;
  }
  invert() {
    const t = this.elements, e = t[0], i = t[1], s = t[2], r = t[3], a = t[4], n = t[5], o = t[6], h = t[7], c = t[8], l = t[9], u = t[10], d = t[11], m = t[12], _ = t[13], f = t[14], p = t[15], x = l * f * h - _ * u * h + _ * o * d - n * f * d - l * o * p + n * u * p, y = m * u * h - c * f * h - m * o * d + a * f * d + c * o * p - a * u * p, M = c * _ * h - m * l * h + m * n * d - a * _ * d - c * n * p + a * l * p, g = m * l * o - c * _ * o - m * n * u + a * _ * u + c * n * f - a * l * f, w = e * x + i * y + s * M + r * g;
    if (0 === w)
      return this.set(0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0);
    const P = 1 / w;
    return t[0] = x * P, t[1] = (_ * u * r - l * f * r - _ * s * d + i * f * d + l * s * p - i * u * p) * P, t[2] = (n * f * r - _ * o * r + _ * s * h - i * f * h - n * s * p + i * o * p) * P, t[3] = (l * o * r - n * u * r - l * s * h + i * u * h + n * s * d - i * o * d) * P, t[4] = y * P, t[5] = (c * f * r - m * u * r + m * s * d - e * f * d - c * s * p + e * u * p) * P, t[6] = (m * o * r - a * f * r - m * s * h + e * f * h + a * s * p - e * o * p) * P, t[7] = (a * u * r - c * o * r + c * s * h - e * u * h - a * s * d + e * o * d) * P, t[8] = M * P, t[9] = (m * l * r - c * _ * r - m * i * d + e * _ * d + c * i * p - e * l * p) * P, t[10] = (a * _ * r - m * n * r + m * i * h - e * _ * h - a * i * p + e * n * p) * P, t[11] = (c * n * r - a * l * r - c * i * h + e * l * h + a * i * d - e * n * d) * P, t[12] = g * P, t[13] = (c * _ * s - m * l * s + m * i * u - e * _ * u - c * i * f + e * l * f) * P, t[14] = (m * n * s - a * _ * s - m * i * o + e * _ * o + a * i * f - e * n * f) * P, t[15] = (a * l * s - c * n * s + c * i * o - e * l * o - a * i * u + e * n * u) * P, this;
  }
  scale(t) {
    const e = this.elements, i = t.x, s = t.y, r = t.z;
    return e[0] *= i, e[4] *= s, e[8] *= r, e[1] *= i, e[5] *= s, e[9] *= r, e[2] *= i, e[6] *= s, e[10] *= r, e[3] *= i, e[7] *= s, e[11] *= r, this;
  }
  getMaxScaleOnAxis() {
    const t = this.elements, e = t[0] * t[0] + t[1] * t[1] + t[2] * t[2], i = t[4] * t[4] + t[5] * t[5] + t[6] * t[6], s = t[8] * t[8] + t[9] * t[9] + t[10] * t[10];
    return Math.sqrt(Math.max(e, i, s));
  }
  makeTranslation(t, e, i) {
    return t.isVector3 ? this.set(1, 0, 0, t.x, 0, 1, 0, t.y, 0, 0, 1, t.z, 0, 0, 0, 1) : this.set(1, 0, 0, t, 0, 1, 0, e, 0, 0, 1, i, 0, 0, 0, 1), this;
  }
  makeRotationX(t) {
    const e = Math.cos(t), i = Math.sin(t);
    return this.set(1, 0, 0, 0, 0, e, -i, 0, 0, i, e, 0, 0, 0, 0, 1), this;
  }
  makeRotationY(t) {
    const e = Math.cos(t), i = Math.sin(t);
    return this.set(e, 0, i, 0, 0, 1, 0, 0, -i, 0, e, 0, 0, 0, 0, 1), this;
  }
  makeRotationZ(t) {
    const e = Math.cos(t), i = Math.sin(t);
    return this.set(e, -i, 0, 0, i, e, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1), this;
  }
  makeRotationAxis(t, e) {
    const i = Math.cos(e), s = Math.sin(e), r = 1 - i, a = t.x, n = t.y, o = t.z, h = r * a, c = r * n;
    return this.set(h * a + i, h * n - s * o, h * o + s * n, 0, h * n + s * o, c * n + i, c * o - s * a, 0, h * o - s * n, c * o + s * a, r * o * o + i, 0, 0, 0, 0, 1), this;
  }
  makeScale(t, e, i) {
    return this.set(t, 0, 0, 0, 0, e, 0, 0, 0, 0, i, 0, 0, 0, 0, 1), this;
  }
  makeShear(t, e, i, s, r, a) {
    return this.set(1, i, r, 0, t, 1, a, 0, e, s, 1, 0, 0, 0, 0, 1), this;
  }
  compose(t, e, i) {
    const s = this.elements, r = e._x, a = e._y, n = e._z, o = e._w, h = r + r, c = a + a, l = n + n, u = r * h, d = r * c, m = r * l, _ = a * c, f = a * l, p = n * l, x = o * h, y = o * c, M = o * l, g = i.x, w = i.y, P = i.z;
    return s[0] = (1 - (_ + p)) * g, s[1] = (d + M) * g, s[2] = (m - y) * g, s[3] = 0, s[4] = (d - M) * w, s[5] = (1 - (u + p)) * w, s[6] = (f + x) * w, s[7] = 0, s[8] = (m + y) * P, s[9] = (f - x) * P, s[10] = (1 - (u + _)) * P, s[11] = 0, s[12] = t.x, s[13] = t.y, s[14] = t.z, s[15] = 1, this;
  }
  decompose(t, e, i) {
    const s = this.elements;
    let r = _v1$5.set(s[0], s[1], s[2]).length();
    const a = _v1$5.set(s[4], s[5], s[6]).length(), n = _v1$5.set(s[8], s[9], s[10]).length();
    this.determinant() < 0 && (r = -r), t.x = s[12], t.y = s[13], t.z = s[14], _m1$2.copy(this);
    const o = 1 / r, h = 1 / a, c = 1 / n;
    return _m1$2.elements[0] *= o, _m1$2.elements[1] *= o, _m1$2.elements[2] *= o, _m1$2.elements[4] *= h, _m1$2.elements[5] *= h, _m1$2.elements[6] *= h, _m1$2.elements[8] *= c, _m1$2.elements[9] *= c, _m1$2.elements[10] *= c, e.setFromRotationMatrix(_m1$2), i.x = r, i.y = a, i.z = n, this;
  }
  makePerspective(t, e, i, s, r, a, n = WebGLCoordinateSystem, o = false) {
    const h = this.elements, c = 2 * r / (e - t), l = 2 * r / (i - s), u = (e + t) / (e - t), d = (i + s) / (i - s);
    let m, _;
    if (o)
      m = r / (a - r), _ = a * r / (a - r);
    else if (n === WebGLCoordinateSystem)
      m = -(a + r) / (a - r), _ = -2 * a * r / (a - r);
    else {
      if (n !== WebGPUCoordinateSystem)
        throw new Error("THREE.Matrix4.makePerspective(): Invalid coordinate system: " + n);
      m = -a / (a - r), _ = -a * r / (a - r);
    }
    return h[0] = c, h[4] = 0, h[8] = u, h[12] = 0, h[1] = 0, h[5] = l, h[9] = d, h[13] = 0, h[2] = 0, h[6] = 0, h[10] = m, h[14] = _, h[3] = 0, h[7] = 0, h[11] = -1, h[15] = 0, this;
  }
  makeOrthographic(t, e, i, s, r, a, n = WebGLCoordinateSystem, o = false) {
    const h = this.elements, c = 2 / (e - t), l = 2 / (i - s), u = -(e + t) / (e - t), d = -(i + s) / (i - s);
    let m, _;
    if (o)
      m = 1 / (a - r), _ = a / (a - r);
    else if (n === WebGLCoordinateSystem)
      m = -2 / (a - r), _ = -(a + r) / (a - r);
    else {
      if (n !== WebGPUCoordinateSystem)
        throw new Error("THREE.Matrix4.makeOrthographic(): Invalid coordinate system: " + n);
      m = -1 / (a - r), _ = -r / (a - r);
    }
    return h[0] = c, h[4] = 0, h[8] = 0, h[12] = u, h[1] = 0, h[5] = l, h[9] = 0, h[13] = d, h[2] = 0, h[6] = 0, h[10] = m, h[14] = _, h[3] = 0, h[7] = 0, h[11] = 0, h[15] = 1, this;
  }
  equals(t) {
    const e = this.elements, i = t.elements;
    for (let t2 = 0; t2 < 16; t2++)
      if (e[t2] !== i[t2])
        return false;
    return true;
  }
  fromArray(t, e = 0) {
    for (let i = 0; i < 16; i++)
      this.elements[i] = t[i + e];
    return this;
  }
  toArray(t = [], e = 0) {
    const i = this.elements;
    return t[e] = i[0], t[e + 1] = i[1], t[e + 2] = i[2], t[e + 3] = i[3], t[e + 4] = i[4], t[e + 5] = i[5], t[e + 6] = i[6], t[e + 7] = i[7], t[e + 8] = i[8], t[e + 9] = i[9], t[e + 10] = i[10], t[e + 11] = i[11], t[e + 12] = i[12], t[e + 13] = i[13], t[e + 14] = i[14], t[e + 15] = i[15], t;
  }
}
const _v1$5 = new Vector3$1(), _m1$2 = new Matrix4(), _zero = new Vector3$1(0, 0, 0), _one = new Vector3$1(1, 1, 1), _x = new Vector3$1(), _y = new Vector3$1(), _z = new Vector3$1(), _vector1 = new Vector3$1(), _vector2 = new Vector3$1(), _normalMatrix = new Matrix3();
class Plane {
  constructor(t = new Vector3$1(1, 0, 0), e = 0) {
    this.isPlane = true, this.normal = t, this.constant = e;
  }
  set(t, e) {
    return this.normal.copy(t), this.constant = e, this;
  }
  setComponents(t, e, i, s) {
    return this.normal.set(t, e, i), this.constant = s, this;
  }
  setFromNormalAndCoplanarPoint(t, e) {
    return this.normal.copy(t), this.constant = -e.dot(this.normal), this;
  }
  setFromCoplanarPoints(t, e, i) {
    const s = _vector1.subVectors(i, e).cross(_vector2.subVectors(t, e)).normalize();
    return this.setFromNormalAndCoplanarPoint(s, t), this;
  }
  copy(t) {
    return this.normal.copy(t.normal), this.constant = t.constant, this;
  }
  normalize() {
    const t = 1 / this.normal.length();
    return this.normal.multiplyScalar(t), this.constant *= t, this;
  }
  negate() {
    return this.constant *= -1, this.normal.negate(), this;
  }
  distanceToPoint(t) {
    return this.normal.dot(t) + this.constant;
  }
  distanceToSphere(t) {
    return this.distanceToPoint(t.center) - t.radius;
  }
  projectPoint(t, e) {
    return e.copy(t).addScaledVector(this.normal, -this.distanceToPoint(t));
  }
  intersectLine(t, e) {
    const i = t.delta(_vector1), s = this.normal.dot(i);
    if (0 === s)
      return 0 === this.distanceToPoint(t.start) ? e.copy(t.start) : null;
    const r = -(t.start.dot(this.normal) + this.constant) / s;
    return r < 0 || r > 1 ? null : e.copy(t.start).addScaledVector(i, r);
  }
  intersectsLine(t) {
    const e = this.distanceToPoint(t.start), i = this.distanceToPoint(t.end);
    return e < 0 && i > 0 || i < 0 && e > 0;
  }
  intersectsBox(t) {
    return t.intersectsPlane(this);
  }
  intersectsSphere(t) {
    return t.intersectsPlane(this);
  }
  coplanarPoint(t) {
    return t.copy(this.normal).multiplyScalar(-this.constant);
  }
  applyMatrix4(t, e) {
    const i = e || _normalMatrix.getNormalMatrix(t), s = this.coplanarPoint(_vector1).applyMatrix4(t), r = this.normal.applyMatrix3(i).normalize();
    return this.constant = -s.dot(r), this;
  }
  translate(t) {
    return this.constant -= t.dot(this.normal), this;
  }
  equals(t) {
    return t.normal.equals(this.normal) && t.constant === this.constant;
  }
  clone() {
    return new this.constructor().copy(this);
  }
}
"undefined" != typeof __THREE_DEVTOOLS__ && __THREE_DEVTOOLS__.dispatchEvent(new CustomEvent("register", { detail: { revision: REVISION } })), "undefined" != typeof window && (window.__THREE__ ? console.warn("WARNING: Multiple instances of Three.js being imported.") : window.__THREE__ = REVISION);
var commonjsGlobal = "undefined" != typeof globalThis ? globalThis : "undefined" != typeof window ? window : "undefined" != typeof global ? global : "undefined" != typeof self ? self : {}, earcut$1 = { exports: {} };
function earcut(t, e, i) {
  i = i || 2;
  var s, r, a, n, o, h, c, l = e && e.length, u = l ? e[0] * i : t.length, d = linkedList(t, 0, u, i, true), m = [];
  if (!d || d.next === d.prev)
    return m;
  if (l && (d = eliminateHoles(t, e, d, i)), t.length > 80 * i) {
    s = a = t[0], r = n = t[1];
    for (var _ = i; _ < u; _ += i)
      (o = t[_]) < s && (s = o), (h = t[_ + 1]) < r && (r = h), o > a && (a = o), h > n && (n = h);
    c = 0 !== (c = Math.max(a - s, n - r)) ? 32767 / c : 0;
  }
  return earcutLinked(d, m, i, s, r, c, 0), m;
}
function linkedList(t, e, i, s, r) {
  var a, n;
  if (r === signedArea(t, e, i, s) > 0)
    for (a = e; a < i; a += s)
      n = insertNode(a, t[a], t[a + 1], n);
  else
    for (a = i - s; a >= e; a -= s)
      n = insertNode(a, t[a], t[a + 1], n);
  return n && equals(n, n.next) && (removeNode(n), n = n.next), n;
}
function filterPoints(t, e) {
  if (!t)
    return t;
  e || (e = t);
  var i, s = t;
  do {
    if (i = false, s.steiner || !equals(s, s.next) && 0 !== area(s.prev, s, s.next))
      s = s.next;
    else {
      if (removeNode(s), (s = e = s.prev) === s.next)
        break;
      i = true;
    }
  } while (i || s !== e);
  return e;
}
function earcutLinked(t, e, i, s, r, a, n) {
  if (t) {
    !n && a && indexCurve(t, s, r, a);
    for (var o, h, c = t; t.prev !== t.next; )
      if (o = t.prev, h = t.next, a ? isEarHashed(t, s, r, a) : isEar(t))
        e.push(o.i / i | 0), e.push(t.i / i | 0), e.push(h.i / i | 0), removeNode(t), t = h.next, c = h.next;
      else if ((t = h) === c) {
        n ? 1 === n ? earcutLinked(t = cureLocalIntersections(filterPoints(t), e, i), e, i, s, r, a, 2) : 2 === n && splitEarcut(t, e, i, s, r, a) : earcutLinked(filterPoints(t), e, i, s, r, a, 1);
        break;
      }
  }
}
function isEar(t) {
  var e = t.prev, i = t, s = t.next;
  if (area(e, i, s) >= 0)
    return false;
  for (var r = e.x, a = i.x, n = s.x, o = e.y, h = i.y, c = s.y, l = r < a ? r < n ? r : n : a < n ? a : n, u = o < h ? o < c ? o : c : h < c ? h : c, d = r > a ? r > n ? r : n : a > n ? a : n, m = o > h ? o > c ? o : c : h > c ? h : c, _ = s.next; _ !== e; ) {
    if (_.x >= l && _.x <= d && _.y >= u && _.y <= m && pointInTriangle(r, o, a, h, n, c, _.x, _.y) && area(_.prev, _, _.next) >= 0)
      return false;
    _ = _.next;
  }
  return true;
}
function isEarHashed(t, e, i, s) {
  var r = t.prev, a = t, n = t.next;
  if (area(r, a, n) >= 0)
    return false;
  for (var o = r.x, h = a.x, c = n.x, l = r.y, u = a.y, d = n.y, m = o < h ? o < c ? o : c : h < c ? h : c, _ = l < u ? l < d ? l : d : u < d ? u : d, f = o > h ? o > c ? o : c : h > c ? h : c, p = l > u ? l > d ? l : d : u > d ? u : d, x = zOrder(m, _, e, i, s), y = zOrder(f, p, e, i, s), M = t.prevZ, g = t.nextZ; M && M.z >= x && g && g.z <= y; ) {
    if (M.x >= m && M.x <= f && M.y >= _ && M.y <= p && M !== r && M !== n && pointInTriangle(o, l, h, u, c, d, M.x, M.y) && area(M.prev, M, M.next) >= 0)
      return false;
    if (M = M.prevZ, g.x >= m && g.x <= f && g.y >= _ && g.y <= p && g !== r && g !== n && pointInTriangle(o, l, h, u, c, d, g.x, g.y) && area(g.prev, g, g.next) >= 0)
      return false;
    g = g.nextZ;
  }
  for (; M && M.z >= x; ) {
    if (M.x >= m && M.x <= f && M.y >= _ && M.y <= p && M !== r && M !== n && pointInTriangle(o, l, h, u, c, d, M.x, M.y) && area(M.prev, M, M.next) >= 0)
      return false;
    M = M.prevZ;
  }
  for (; g && g.z <= y; ) {
    if (g.x >= m && g.x <= f && g.y >= _ && g.y <= p && g !== r && g !== n && pointInTriangle(o, l, h, u, c, d, g.x, g.y) && area(g.prev, g, g.next) >= 0)
      return false;
    g = g.nextZ;
  }
  return true;
}
function cureLocalIntersections(t, e, i) {
  var s = t;
  do {
    var r = s.prev, a = s.next.next;
    !equals(r, a) && intersects(r, s, s.next, a) && locallyInside(r, a) && locallyInside(a, r) && (e.push(r.i / i | 0), e.push(s.i / i | 0), e.push(a.i / i | 0), removeNode(s), removeNode(s.next), s = t = a), s = s.next;
  } while (s !== t);
  return filterPoints(s);
}
function splitEarcut(t, e, i, s, r, a) {
  var n = t;
  do {
    for (var o = n.next.next; o !== n.prev; ) {
      if (n.i !== o.i && isValidDiagonal(n, o)) {
        var h = splitPolygon(n, o);
        return n = filterPoints(n, n.next), h = filterPoints(h, h.next), earcutLinked(n, e, i, s, r, a, 0), void earcutLinked(h, e, i, s, r, a, 0);
      }
      o = o.next;
    }
    n = n.next;
  } while (n !== t);
}
function eliminateHoles(t, e, i, s) {
  var r, a, n, o = [];
  for (r = 0, a = e.length; r < a; r++)
    (n = linkedList(t, e[r] * s, r < a - 1 ? e[r + 1] * s : t.length, s, false)) === n.next && (n.steiner = true), o.push(getLeftmost(n));
  for (o.sort(compareX), r = 0; r < o.length; r++)
    i = eliminateHole(o[r], i);
  return i;
}
function compareX(t, e) {
  return t.x - e.x;
}
function eliminateHole(t, e) {
  var i = findHoleBridge(t, e);
  if (!i)
    return e;
  var s = splitPolygon(i, t);
  return filterPoints(s, s.next), filterPoints(i, i.next);
}
function findHoleBridge(t, e) {
  var i, s = e, r = t.x, a = t.y, n = -1 / 0;
  do {
    if (a <= s.y && a >= s.next.y && s.next.y !== s.y) {
      var o = s.x + (a - s.y) * (s.next.x - s.x) / (s.next.y - s.y);
      if (o <= r && o > n && (n = o, i = s.x < s.next.x ? s : s.next, o === r))
        return i;
    }
    s = s.next;
  } while (s !== e);
  if (!i)
    return null;
  var h, c = i, l = i.x, u = i.y, d = 1 / 0;
  s = i;
  do {
    r >= s.x && s.x >= l && r !== s.x && pointInTriangle(a < u ? r : n, a, l, u, a < u ? n : r, a, s.x, s.y) && (h = Math.abs(a - s.y) / (r - s.x), locallyInside(s, t) && (h < d || h === d && (s.x > i.x || s.x === i.x && sectorContainsSector(i, s))) && (i = s, d = h)), s = s.next;
  } while (s !== c);
  return i;
}
function sectorContainsSector(t, e) {
  return area(t.prev, t, e.prev) < 0 && area(e.next, t, t.next) < 0;
}
function indexCurve(t, e, i, s) {
  var r = t;
  do {
    0 === r.z && (r.z = zOrder(r.x, r.y, e, i, s)), r.prevZ = r.prev, r.nextZ = r.next, r = r.next;
  } while (r !== t);
  r.prevZ.nextZ = null, r.prevZ = null, sortLinked(r);
}
function sortLinked(t) {
  var e, i, s, r, a, n, o, h, c = 1;
  do {
    for (i = t, t = null, a = null, n = 0; i; ) {
      for (n++, s = i, o = 0, e = 0; e < c && (o++, s = s.nextZ); e++)
        ;
      for (h = c; o > 0 || h > 0 && s; )
        0 !== o && (0 === h || !s || i.z <= s.z) ? (r = i, i = i.nextZ, o--) : (r = s, s = s.nextZ, h--), a ? a.nextZ = r : t = r, r.prevZ = a, a = r;
      i = s;
    }
    a.nextZ = null, c *= 2;
  } while (n > 1);
  return t;
}
function zOrder(t, e, i, s, r) {
  return (t = 1431655765 & ((t = 858993459 & ((t = 252645135 & ((t = 16711935 & ((t = (t - i) * r | 0) | t << 8)) | t << 4)) | t << 2)) | t << 1)) | (e = 1431655765 & ((e = 858993459 & ((e = 252645135 & ((e = 16711935 & ((e = (e - s) * r | 0) | e << 8)) | e << 4)) | e << 2)) | e << 1)) << 1;
}
function getLeftmost(t) {
  var e = t, i = t;
  do {
    (e.x < i.x || e.x === i.x && e.y < i.y) && (i = e), e = e.next;
  } while (e !== t);
  return i;
}
function pointInTriangle(t, e, i, s, r, a, n, o) {
  return (r - n) * (e - o) >= (t - n) * (a - o) && (t - n) * (s - o) >= (i - n) * (e - o) && (i - n) * (a - o) >= (r - n) * (s - o);
}
function isValidDiagonal(t, e) {
  return t.next.i !== e.i && t.prev.i !== e.i && !intersectsPolygon(t, e) && (locallyInside(t, e) && locallyInside(e, t) && middleInside(t, e) && (area(t.prev, t, e.prev) || area(t, e.prev, e)) || equals(t, e) && area(t.prev, t, t.next) > 0 && area(e.prev, e, e.next) > 0);
}
function area(t, e, i) {
  return (e.y - t.y) * (i.x - e.x) - (e.x - t.x) * (i.y - e.y);
}
function equals(t, e) {
  return t.x === e.x && t.y === e.y;
}
function intersects(t, e, i, s) {
  var r = sign$1(area(t, e, i)), a = sign$1(area(t, e, s)), n = sign$1(area(i, s, t)), o = sign$1(area(i, s, e));
  return r !== a && n !== o || (!(0 !== r || !onSegment(t, i, e)) || (!(0 !== a || !onSegment(t, s, e)) || (!(0 !== n || !onSegment(i, t, s)) || !(0 !== o || !onSegment(i, e, s)))));
}
function onSegment(t, e, i) {
  return e.x <= Math.max(t.x, i.x) && e.x >= Math.min(t.x, i.x) && e.y <= Math.max(t.y, i.y) && e.y >= Math.min(t.y, i.y);
}
function sign$1(t) {
  return t > 0 ? 1 : t < 0 ? -1 : 0;
}
function intersectsPolygon(t, e) {
  var i = t;
  do {
    if (i.i !== t.i && i.next.i !== t.i && i.i !== e.i && i.next.i !== e.i && intersects(i, i.next, t, e))
      return true;
    i = i.next;
  } while (i !== t);
  return false;
}
function locallyInside(t, e) {
  return area(t.prev, t, t.next) < 0 ? area(t, e, t.next) >= 0 && area(t, t.prev, e) >= 0 : area(t, e, t.prev) < 0 || area(t, t.next, e) < 0;
}
function middleInside(t, e) {
  var i = t, s = false, r = (t.x + e.x) / 2, a = (t.y + e.y) / 2;
  do {
    i.y > a != i.next.y > a && i.next.y !== i.y && r < (i.next.x - i.x) * (a - i.y) / (i.next.y - i.y) + i.x && (s = !s), i = i.next;
  } while (i !== t);
  return s;
}
function splitPolygon(t, e) {
  var i = new Node(t.i, t.x, t.y), s = new Node(e.i, e.x, e.y), r = t.next, a = e.prev;
  return t.next = e, e.prev = t, i.next = r, r.prev = i, s.next = i, i.prev = s, a.next = s, s.prev = a, s;
}
function insertNode(t, e, i, s) {
  var r = new Node(t, e, i);
  return s ? (r.next = s.next, r.prev = s, s.next.prev = r, s.next = r) : (r.prev = r, r.next = r), r;
}
function removeNode(t) {
  t.next.prev = t.prev, t.prev.next = t.next, t.prevZ && (t.prevZ.nextZ = t.nextZ), t.nextZ && (t.nextZ.prevZ = t.prevZ);
}
function Node(t, e, i) {
  this.i = t, this.x = e, this.y = i, this.prev = null, this.next = null, this.z = 0, this.prevZ = null, this.nextZ = null, this.steiner = false;
}
function signedArea(t, e, i, s) {
  for (var r = 0, a = e, n = i - s; a < i; a += s)
    r += (t[n] - t[a]) * (t[a + 1] + t[n + 1]), n = a;
  return r;
}
function defined$1(t) {
  return null != t;
}
function defaultValue(t, e) {
  return null != t ? t : e;
}
function DeveloperError(t) {
  let e;
  this.name = "DeveloperError", this.message = t;
  try {
    throw new Error();
  } catch (t2) {
    e = t2.stack;
  }
  this.stack = e;
}
earcut$1.exports = earcut, earcut$1.exports.default = earcut, earcut.deviation = function(t, e, i, s) {
  var r = e && e.length, a = r ? e[0] * i : t.length, n = Math.abs(signedArea(t, 0, a, i));
  if (r)
    for (var o = 0, h = e.length; o < h; o++) {
      var c = e[o] * i, l = o < h - 1 ? e[o + 1] * i : t.length;
      n -= Math.abs(signedArea(t, c, l, i));
    }
  var u = 0;
  for (o = 0; o < s.length; o += 3) {
    var d = s[o] * i, m = s[o + 1] * i, _ = s[o + 2] * i;
    u += Math.abs((t[d] - t[_]) * (t[m + 1] - t[d + 1]) - (t[d] - t[m]) * (t[_ + 1] - t[d + 1]));
  }
  return 0 === n && 0 === u ? 0 : Math.abs((u - n) / n);
}, earcut.flatten = function(t) {
  for (var e = t[0][0].length, i = { vertices: [], holes: [], dimensions: e }, s = 0, r = 0; r < t.length; r++) {
    for (var a = 0; a < t[r].length; a++)
      for (var n = 0; n < e; n++)
        i.vertices.push(t[r][a][n]);
    r > 0 && (s += t[r - 1].length, i.holes.push(s));
  }
  return i;
}, defaultValue.EMPTY_OBJECT = Object.freeze({}), defined$1(Object.create) && (DeveloperError.prototype = Object.create(Error.prototype), DeveloperError.prototype.constructor = DeveloperError), DeveloperError.prototype.toString = function() {
  let t = this.name + ": " + this.message;
  return defined$1(this.stack) && (t += "\n" + this.stack.toString()), t;
}, DeveloperError.throwInstantiationError = function() {
  throw new DeveloperError("This function defines an interface and should not be called directly.");
};
const _CesiumMath = class {
  static equalsEpsilon(t, e, i, s) {
    i = defaultValue(i, 0), s = defaultValue(s, i);
    const r = Math.abs(t - e);
    return r <= s || r <= i * Math.max(Math.abs(t), Math.abs(e));
  }
  static toRadians(t) {
    return MathUtils.degToRad(t);
  }
  static clamp(t, e, i) {
    return t < e ? e : t > i ? i : t;
  }
  static acosClamped(t) {
    return Math.acos(_CesiumMath.clamp(t, -1, 1));
  }
  static asinClamped(t) {
    return Math.asin(_CesiumMath.clamp(t, -1, 1));
  }
  static sign(t) {
    return Math.sign(t);
  }
  static zeroToTwoPi(t) {
    if (t >= 0 && t <= _CesiumMath.TWO_PI)
      return t;
    const e = _CesiumMath.mod(t, _CesiumMath.TWO_PI);
    return Math.abs(e) < _CesiumMath.EPSILON14 && Math.abs(t) > _CesiumMath.EPSILON14 ? _CesiumMath.TWO_PI : e;
  }
  static mod(t, e) {
    return _CesiumMath.sign(t) === _CesiumMath.sign(e) && Math.abs(t) < Math.abs(e) ? t : (t % e + e) % e;
  }
  static chordLength(t, e) {
    return 2 * e * Math.sin(0.5 * t);
  }
  static negativePiToPi(t) {
    if (!defined$1(t))
      throw new DeveloperError("angle is required.");
    return t >= -_CesiumMath.PI && t <= _CesiumMath.PI ? t : _CesiumMath.zeroToTwoPi(t + _CesiumMath.PI) - _CesiumMath.PI;
  }
  static normalize(t, e, i) {
    return 0 === (i = Math.max(i - e, 0)) ? 0 : _CesiumMath.clamp((t - e) / i, 0, 1);
  }
};
let CesiumMath = _CesiumMath;
__publicField(CesiumMath, "EPSILON1", 0.1);
__publicField(CesiumMath, "EPSILON2", 0.01);
__publicField(CesiumMath, "EPSILON3", 1e-3);
__publicField(CesiumMath, "EPSILON4", 1e-4);
__publicField(CesiumMath, "EPSILON5", 1e-5);
__publicField(CesiumMath, "EPSILON6", 1e-6);
__publicField(CesiumMath, "EPSILON7", 1e-7);
__publicField(CesiumMath, "EPSILON8", 1e-8);
__publicField(CesiumMath, "EPSILON9", 1e-9);
__publicField(CesiumMath, "EPSILON10", 1e-10);
__publicField(CesiumMath, "EPSILON11", 1e-11);
__publicField(CesiumMath, "EPSILON12", 1e-12);
__publicField(CesiumMath, "EPSILON13", 1e-13);
__publicField(CesiumMath, "EPSILON14", 1e-14);
__publicField(CesiumMath, "EPSILON15", 1e-15);
__publicField(CesiumMath, "EPSILON16", 1e-16);
__publicField(CesiumMath, "EPSILON17", 1e-17);
__publicField(CesiumMath, "EPSILON18", 1e-18);
__publicField(CesiumMath, "EPSILON19", 1e-19);
__publicField(CesiumMath, "EPSILON20", 1e-20);
__publicField(CesiumMath, "EPSILON21", 1e-21);
__publicField(CesiumMath, "PI", Math.PI);
__publicField(CesiumMath, "ONE_OVER_PI", 1 / Math.PI);
__publicField(CesiumMath, "PI_OVER_TWO", Math.PI / 2);
__publicField(CesiumMath, "PI_OVER_THREE", Math.PI / 3);
__publicField(CesiumMath, "PI_OVER_FOUR", Math.PI / 4);
__publicField(CesiumMath, "PI_OVER_SIX", Math.PI / 6);
__publicField(CesiumMath, "THREE_PI_OVER_TWO", 3 * Math.PI / 2);
__publicField(CesiumMath, "TWO_PI", 2 * Math.PI);
__publicField(CesiumMath, "ONE_OVER_TWO_PI", 1 / (2 * Math.PI));
__publicField(CesiumMath, "RADIANS_PER_DEGREE", Math.PI / 180);
const angleBetweenScratch$1 = new Vector2(), angleBetweenScratch2$1 = new Vector2();
const _Cartesian2 = class {
  static clone(t, e) {
    return e.copy(t), e;
  }
  static fromElements(t, e, i) {
    return i || (i = new Vector2()), i.set(t, e), i;
  }
  static lerp(t, e, i, s) {
    return s || (s = new Vector2()), s.lerpVectors(t, e, i), s;
  }
  static equalsEpsilon(t, e, i, s) {
    return t === e || defined$1(t) && defined$1(e) && CesiumMath.equalsEpsilon(t.x, e.x, i, s) && CesiumMath.equalsEpsilon(t.y, e.y, i, s);
  }
  static equals(t, e) {
    return t.equals(e);
  }
  static dot(t, e) {
    return t.dot(e);
  }
  static normalize(t, e) {
    return t === e ? (t.normalize(), t) : (e.copy(t), e.normalize(), e);
  }
  static add(t, e, i) {
    return i || (i = new Vector2()), i.addVectors(t, e);
  }
  static multiplyByScalar(t, e, i) {
    return i || (i = new Vector2()), i.copy(t).multiplyScalar(e), i;
  }
  static subtract(t, e, i) {
    return i || (i = new Vector2()), i.subVectors(t, e), i;
  }
  static distance(t, e) {
    return t.distanceTo(e);
  }
  static angleBetween(t, e) {
    return _Cartesian2.normalize(t, angleBetweenScratch$1), _Cartesian2.normalize(e, angleBetweenScratch2$1), CesiumMath.acosClamped(_Cartesian2.dot(angleBetweenScratch$1, angleBetweenScratch2$1));
  }
};
let Cartesian2 = _Cartesian2;
__publicField(Cartesian2, "ZERO", new Vector2());
Cartesian2.fromCartesian3 = Cartesian2.clone, Cartesian2.fromCartesian4 = Cartesian2.clone;
const mostOrthogonalAxisScratch = new Vector3$1();
let scratchN$1 = new Vector3$1(), scratchK$1 = new Vector3$1();
const wgs84RadiiSquared = new Vector3$1(40680631590769, 40680631590769, 40408299984661445e-3), angleBetweenScratch = new Vector3$1(), angleBetweenScratch2 = new Vector3$1();
const _Cartesian3 = class {
  constructor() {
    __publicField(this, "COLUMN0ROW0", 0);
    __publicField(this, "COLUMN0ROW1", 1);
    __publicField(this, "COLUMN0ROW2", 2);
    __publicField(this, "COLUMN1ROW0", 3);
    __publicField(this, "COLUMN1ROW1", 4);
    __publicField(this, "COLUMN1ROW2", 5);
    __publicField(this, "COLUMN2ROW0", 6);
    __publicField(this, "COLUMN2ROW1", 7);
    __publicField(this, "COLUMN2ROW2", 8);
  }
  static clone(t, e) {
    if (t)
      return e.copy(t), e;
  }
  static equals(t, e) {
    return !(!defined$1(t) || !defined$1(e)) && t.equals(e);
  }
  static normalize(t, e) {
    return t === e ? (t.normalize(), t) : (e.copy(t), e.normalize(), e);
  }
  static add(t, e, i) {
    return i || (i = new Vector3$1()), i.addVectors(t, e);
  }
  static dot(t, e) {
    return t.dot(e);
  }
  static cross(t, e, i) {
    return i || (i = new Vector3$1()), i.crossVectors(t, e), i;
  }
  static magnitudeSquared(t) {
    return t.lengthSq();
  }
  static multiplyByScalar(t, e, i) {
    return i || (i = new Vector3$1()), i.copy(t).multiplyScalar(e), i;
  }
  static divideByScalar(t, e, i) {
    return i || (i = new Vector3$1()), i.x = t.x / e, i.y = t.y / e, i.z = t.z / e, i;
  }
  static subtract(t, e, i) {
    return i || (i = new Vector3$1()), i.subVectors(t, e), i;
  }
  static distance(t, e) {
    return t.distanceTo(e);
  }
  static negate(t, e) {
    return e || (e = new Vector3$1()), e.copy(t), e.negate(), e;
  }
  static multiplyComponents(t, e, i) {
    return i || (i = new Vector3$1()), i.multiplyVectors(t, e), i;
  }
  static magnitude(t) {
    return t.length();
  }
  static equalsEpsilon(t, e, i, s) {
    return t === e || defined$1(t) && defined$1(e) && CesiumMath.equalsEpsilon(t.x, e.x, i, s) && CesiumMath.equalsEpsilon(t.y, e.y, i, s) && CesiumMath.equalsEpsilon(t.z, e.z, i, s);
  }
  static fromCartesian4(t, e) {
    return e || (e = new Vector3$1()), e.set(t.x, t.y, t.z), e;
  }
  static fromElements(t, e, i, s) {
    return s || (s = new Vector3$1()), s.set(t, e, i), s;
  }
  static fromRadians(t, e, i, s, r) {
    i = defaultValue(i, 0);
    const a = defined$1(s) ? s.radiiSquared : wgs84RadiiSquared, n = Math.cos(e);
    scratchN$1.x = n * Math.cos(t), scratchN$1.y = n * Math.sin(t), scratchN$1.z = Math.sin(e), scratchN$1 = _Cartesian3.normalize(scratchN$1, scratchN$1), _Cartesian3.multiplyComponents(a, scratchN$1, scratchK$1);
    const o = Math.sqrt(_Cartesian3.dot(scratchN$1, scratchK$1));
    return scratchK$1 = _Cartesian3.divideByScalar(scratchK$1, o, scratchK$1), scratchN$1 = _Cartesian3.multiplyByScalar(scratchN$1, i, scratchN$1), defined$1(r) || (r = new Vector3$1()), _Cartesian3.add(scratchK$1, scratchN$1, r);
  }
  static angleBetween(t, e) {
    _Cartesian3.normalize(t, angleBetweenScratch), _Cartesian3.normalize(e, angleBetweenScratch2);
    const i = _Cartesian3.dot(angleBetweenScratch, angleBetweenScratch2), s = _Cartesian3.magnitude(_Cartesian3.cross(angleBetweenScratch, angleBetweenScratch2, angleBetweenScratch));
    return Math.atan2(s, i);
  }
  static fromDegrees(t, e, i, s, r) {
    return t = CesiumMath.toRadians(t), e = CesiumMath.toRadians(e), _Cartesian3.fromRadians(t, e, i, s, r);
  }
};
let Cartesian3 = _Cartesian3;
__publicField(Cartesian3, "ZERO", Object.freeze(new Vector3$1()));
__publicField(Cartesian3, "UNIT_X", Object.freeze(new Vector3$1(1, 0, 0)));
__publicField(Cartesian3, "UNIT_Y", Object.freeze(new Vector3$1(0, 1, 0)));
__publicField(Cartesian3, "UNIT_Z", Object.freeze(new Vector3$1(0, 0, 1)));
__publicField(Cartesian3, "abs", function(t, e) {
  return e.x = Math.abs(t.x), e.y = Math.abs(t.y), e.z = Math.abs(t.z), e;
});
__publicField(Cartesian3, "mostOrthogonalAxis", function(t, e) {
  const i = _Cartesian3.normalize(t, mostOrthogonalAxisScratch);
  return _Cartesian3.abs(i, i), e = i.x <= i.y ? i.x <= i.z ? _Cartesian3.clone(_Cartesian3.UNIT_X, e) : _Cartesian3.clone(_Cartesian3.UNIT_Z, e) : i.y <= i.z ? _Cartesian3.clone(_Cartesian3.UNIT_Y, e) : _Cartesian3.clone(_Cartesian3.UNIT_Z, e);
});
class Rectangle {
  constructor(t, e, i, s) {
    this.west = t || 0, this.south = e || 0, this.east = i || 0, this.north = s || 0;
  }
  get width() {
    return Rectangle.computeWidth(this);
  }
  get height() {
    return Rectangle.computeHeight(this);
  }
}
Rectangle.fromDegrees = function(t, e, i, s, r) {
  return t = MathUtils.degToRad(defaultValue(t, 0)), e = MathUtils.degToRad(defaultValue(e, 0)), i = MathUtils.degToRad(defaultValue(i, 0)), s = MathUtils.degToRad(defaultValue(s, 0)), defined$1(r) ? (r.west = t, r.south = e, r.east = i, r.north = s, r) : new Rectangle(t, e, i, s);
}, Rectangle.computeWidth = function(t) {
  let e = t.east;
  const i = t.west;
  return e < i && (e += CesiumMath.TWO_PI), e - i;
}, Rectangle.computeHeight = function(t) {
  return t.north - t.south;
}, Rectangle.clone = function(t, e) {
  if (defined$1(t))
    return defined$1(e) ? (e.west = t.west, e.south = t.south, e.east = t.east, e.north = t.north, e) : new Rectangle(t.west, t.south, t.east, t.north);
}, Rectangle.southwest = function(t, e) {
  return defined$1(e) ? (e.x = t.west, e.y = t.south, e.z = 0, e) : new Vector3$1(t.west, t.south);
}, Rectangle.northeast = function(t, e) {
  return defined$1(e) ? (e.x = t.east, e.y = t.north, e.z = 0, e) : new Vector3$1(t.east, t.north);
}, Rectangle.southeast = function(t, e) {
  return defined$1(e) ? (e.x = t.east, e.y = t.south, e.z = 0, e) : new Vector3$1(t.east, t.south);
}, Rectangle.northwest = function(t, e) {
  return defined$1(e) ? (e.x = t.west, e.y = t.north, e.z = 0, e) : new Vector3$1(t.west, t.north);
}, Rectangle.center = function(t, e) {
  let i = t.east;
  const s = t.west;
  i < s && (i += CesiumMath.TWO_PI);
  const r = CesiumMath.negativePiToPi(0.5 * (s + i)), a = 0.5 * (t.south + t.north);
  return defined$1(e) ? (e.x = r, e.y = a, e.z = 0, e) : new Vector3$1(r, a);
}, Rectangle.contains = function(t, e) {
  let i = e.x;
  const s = e.y, r = t.west;
  let a = t.east;
  return a < r && (a += CesiumMath.TWO_PI, i < 0 && (i += CesiumMath.TWO_PI)), (i > r || CesiumMath.equalsEpsilon(i, r, CesiumMath.EPSILON14)) && (i < a || CesiumMath.equalsEpsilon(i, a, CesiumMath.EPSILON14)) && s >= t.south && s <= t.north;
};
const maxPI = Math.PI + 1e-5, minPI = -Math.PI - 1e-5, maxPIOverTwo = CesiumMath.PI_OVER_TWO + 1e-5, minPIOverTwo = -CesiumMath.PI_OVER_TWO - 1e-5;
Rectangle.fromBox = function(t, e, i = false) {
  const s = t.min, r = t.max;
  let a = s.x / 180 * Math.PI, n = s.y / 180 * Math.PI, o = r.x / 180 * Math.PI, h = r.y / 180 * Math.PI;
  return i && (a < minPI && (a = -Math.PI), a > maxPI && (a = Math.PI), n < minPIOverTwo && (n = -CesiumMath.PI_OVER_TWO), n > maxPIOverTwo && (n = CesiumMath.PI_OVER_TWO), o > maxPI && (o = Math.PI), o < minPI && (o = -Math.PI), h > maxPIOverTwo && (h = CesiumMath.PI_OVER_TWO), h < minPIOverTwo && (h = -CesiumMath.PI_OVER_TWO)), defined$1(e) ? (e.west = a, e.south = n, e.east = o, e.north = h, e) : new Rectangle(a, n, o, h);
}, Rectangle.MAX_VALUE = Object.freeze(new Rectangle(-Math.PI, -CesiumMath.PI_OVER_TWO, Math.PI, CesiumMath.PI_OVER_TWO));
const scaleToGeodeticSurfaceIntersection = new Vector3$1(), scaleToGeodeticSurfaceGradient = new Vector3$1();
function scaleToGeodeticSurface(t, e, i, s, r) {
  const a = t.x, n = t.y, o = t.z, h = e.x, c = e.y, l = e.z, u = a * a * h * h, d = n * n * c * c, m = o * o * l * l, _ = u + d + m, f = Math.sqrt(1 / _), p = scaleToGeodeticSurfaceIntersection.copy(t).multiplyScalar(f);
  if (_ < s)
    return r || (r = new Vector3$1()), isFinite(f) ? r.copy(p) : void 0;
  const x = i.x, y = i.y, M = i.z, g = scaleToGeodeticSurfaceGradient;
  g.x = p.x * x * 2, g.y = p.y * y * 2, g.z = p.z * M * 2;
  let w, P, S, C, E, v, b, z, T, A2, O, G = (1 - f) * t.length() / (0.5 * g.length()), N = 0;
  do {
    G -= N, S = 1 / (1 + G * x), C = 1 / (1 + G * y), E = 1 / (1 + G * M), v = S * S, b = C * C, z = E * E, T = v * S, A2 = b * C, O = z * E, w = u * v + d * b + m * z - 1, P = u * T * x + d * A2 * y + m * O * M, N = w / (-2 * P);
  } while (Math.abs(w) > 1e-12);
  return r ? (r.x = a * S, r.y = n * C, r.z = o * E, r) : new Vector3$1(a * S, n * C, o * E);
}
const cartesianToCartographicN = new Vector3$1(), cartesianToCartographicP = new Vector3$1(), cartesianToCartographicH = new Vector3$1(), wgs84OneOverRadii = new Vector3$1(1 / 6378137, 1 / 6378137, 1 / 6356752314245179e-9), wgs84OneOverRadiiSquared = new Vector3$1(1 / 40680631590769, 1 / 40680631590769, 1 / 40408299984661445e-3), wgs84CenterToleranceSquared = CesiumMath.EPSILON1;
const _Cartographic = class {
  static fromRadians(t, e, i, s) {
    return i = defaultValue(i, 0), defined$1(s) ? (s.x = t, s.y = e, s.z = i, s) : new Vector3$1(t, e, i);
  }
  static fromDegrees(t, e, i, s) {
    return t = CesiumMath.toRadians(t), e = CesiumMath.toRadians(e), _Cartographic.fromRadians(t, e, i, s);
  }
  static fromCartesian(t, e, i) {
    const s = defined$1(e) ? e.oneOverRadii : wgs84OneOverRadii, r = defined$1(e) ? e.oneOverRadiiSquared : wgs84OneOverRadiiSquared, a = scaleToGeodeticSurface(t, s, r, defined$1(e) ? e._centerToleranceSquared : wgs84CenterToleranceSquared, cartesianToCartographicP);
    if (!defined$1(a))
      return;
    let n = Cartesian3.multiplyComponents(a, r, cartesianToCartographicN);
    n = Cartesian3.normalize(n, n);
    const o = Cartesian3.subtract(t, a, cartesianToCartographicH), h = Math.atan2(n.y, n.x), c = Math.asin(n.z), l = CesiumMath.sign(Cartesian3.dot(o, t)) * Cartesian3.magnitude(o);
    return defined$1(i) ? (i.x = h, i.y = c, i.z = l, i) : new Vector3$1(h, c, l);
  }
  static toCartesian(t, e, i) {
    return Cartesian3.fromRadians(t.x, t.y, t.z, e, i);
  }
  static clone(t, e) {
    if (defined$1(t))
      return defined$1(e) ? (e.x = t.x, e.y = t.y, e.z = t.z, e) : new Vector3$1(t.x, t.y, t.z);
  }
  static equals(t, e) {
    return t === e || defined$1(t) && defined$1(e) && t.x === e.x && t.y === e.y && t.z === e.z;
  }
  static equalsEpsilon(t, e, i) {
    return i = defaultValue(i, 0), t === e || defined$1(t) && defined$1(e) && CesiumMath.equalsEpsilon(t.x, e.x, i) && CesiumMath.equalsEpsilon(t.y, e.y, i) && CesiumMath.equalsEpsilon(t.z, e.z, i);
  }
};
let Cartographic = _Cartographic;
__publicField(Cartographic, "fromRadians", function(t, e, i, s) {
  return i = defaultValue(i, 0), defined$1(s) ? (s.x = t, s.y = e, s.z = i, s) : new Vector3$1(t, e, i);
});
__publicField(Cartographic, "fromDegrees", function(t, e, i, s) {
  return t = CesiumMath.toRadians(t), e = CesiumMath.toRadians(e), _Cartographic.fromRadians(t, e, i, s);
});
__publicField(Cartographic, "ZERO", Object.freeze(new Vector3$1(0, 0, 0)));
const _inputVector3 = new Vector3$1(), _outputVector3 = new Vector3$1();
class Ellipsoid {
  constructor(t, e, i) {
    this._radii = new Vector3$1(t, e, i), this._radiiSquared = new Vector3$1(t * t, e * e, i * i), this._radiiToTheFourth = new Vector3$1(t * t * t * t, e * e * e * e, i * i * i * i), this._oneOverRadii = new Vector3$1(0 === t ? 0 : 1 / t, 0 === e ? 0 : 1 / e, 0 === i ? 0 : 1 / i), this._oneOverRadiiSquared = new Vector3$1(0 === t ? 0 : 1 / (t * t), 0 === e ? 0 : 1 / (e * e), 0 === i ? 0 : 1 / (i * i)), this._minimumRadius = Math.min(t, e, i), this._maximumRadius = Math.max(t, e, i), this._centerToleranceSquared = 0.1, 0 !== this._radiiSquared.z && (this._squaredXOverSquaredZ = this._radiiSquared.x / this._radiiSquared.z);
  }
  static fromCartesian3(t) {
    return new Ellipsoid(t.x, t.y, t.z);
  }
  geodeticSurfaceNormalCartographic(t, e) {
    e || (e = new Vector3$1());
    const i = t.x, s = t.y, r = Math.cos(s), a = r * Math.cos(i), n = r * Math.sin(i), o = Math.sin(s);
    return e.set(a, n, o), e.normalize(), e;
  }
  cartographicDegreeToCartesian(t, e) {
    return _inputVector3.set(MathUtils.degToRad(t.x), MathUtils.degToRad(t.y), t.z), this.cartographicToCartesian(_inputVector3, e);
  }
  cartographicToCartesian(t, e) {
    const i = this.geodeticSurfaceNormalCartographic(t);
    e || (e = new Vector3$1()), e.multiplyVectors(this._radiiSquared, i);
    const s = Math.sqrt(i.clone().dot(e));
    return e.divideScalar(s), i.multiplyScalar(t.z), e.add(i), e;
  }
  cartesianToCartographicDegree(t, e) {
    const i = this.cartesianToCartographic(t, e);
    if (i)
      return (e = i).x = MathUtils.radToDeg(e.x), e.y = MathUtils.radToDeg(e.y), e;
  }
  scaleToGeodeticSurface(t, e) {
    return scaleToGeodeticSurface(t, this._oneOverRadii, this._oneOverRadiiSquared, this._centerToleranceSquared, e);
  }
  scaleToGeocentricSurface(t, e) {
    e || (e = new Vector3$1());
    const i = t.x, s = t.y, r = t.z, a = this._oneOverRadiiSquared, n = 1 / Math.sqrt(i * i * a.x + s * s * a.y + r * r * a.z);
    return e.copy(t).multiplyScalar(n);
  }
  cartesianToCartographic(t, e) {
    const i = this.scaleToGeodeticSurface(t, _outputVector3);
    if (!i)
      return;
    const s = this.geodeticSurfaceNormal(i), r = t.clone();
    r.sub(i);
    const a = Math.atan2(s.y, s.x), n = Math.asin(s.z), o = Math.sign(r.dot(t)) * r.length();
    return e || (e = new Vector3$1()), e.set(a, n, o), e;
  }
  geodeticSurfaceNormal(t, e) {
    return defined$1(e) || (e = new Vector3$1()), e.multiplyVectors(t, this._oneOverRadiiSquared), e.normalize(), e;
  }
  getSurfaceNormalIntersectionWithZAxis(t, e, i) {
    e = defaultValue(e, 0);
    const s = this._squaredXOverSquaredZ;
    if (defined$1(i) || (i = new Vector3$1()), i.x = 0, i.y = 0, i.z = t.z * (1 - s), !(Math.abs(i.z) >= this._radii.z - e))
      return i;
  }
  transformPositionToScaledSpace(t, e) {
    return Cartesian3.multiplyComponents(t, this._oneOverRadii, e);
  }
  static clone(t, e) {
    if (!t)
      return;
    const i = t._radii;
    return e ? (Cartesian3.clone(i, e._radii), Cartesian3.clone(t._radiiSquared, e._radiiSquared), Cartesian3.clone(t._radiiToTheFourth, e._radiiToTheFourth), Cartesian3.clone(t._oneOverRadii, e._oneOverRadii), Cartesian3.clone(t._oneOverRadiiSquared, e._oneOverRadiiSquared), e._minimumRadius = t._minimumRadius, e._maximumRadius = t._maximumRadius, e._centerToleranceSquared = t._centerToleranceSquared, e) : new Ellipsoid(i.x, i.y, i.z);
  }
  get radii() {
    return this._radii;
  }
  get radiiSquared() {
    return this._radiiSquared;
  }
  get radiiToTheFourth() {
    return this.radiiToTheFourth;
  }
  get oneOverRadii() {
    return this._oneOverRadii;
  }
  get oneOverRadiiSquared() {
    return this._oneOverRadiiSquared;
  }
  get maximumRadius() {
    return this._maximumRadius;
  }
  get minimumRadius() {
    return this._minimumRadius;
  }
}
Ellipsoid.WGS84 = Object.freeze(new Ellipsoid(6378137, 6378137, 6356752314245179e-9));
let scratchHPRQuaternion$1 = new Quaternion$1(), scratchHeadingQuaternion = new Quaternion$1(), scratchPitchQuaternion = new Quaternion$1(), scratchRollQuaternion = new Quaternion$1(), fromAxisAngleScratch = new Vector3$1();
class StaticQuaternion {
  static fromAxisAngle(t, e, i) {
    return i || (i = new Quaternion$1()), fromAxisAngleScratch.copy(t), fromAxisAngleScratch.normalize(), i.setFromAxisAngle(fromAxisAngleScratch, e), i;
  }
  static multiply(t, e, i) {
    return i || (i = new Quaternion$1()), i.multiplyQuaternions(t, e), i;
  }
  static fromHeadingPitchRoll(t, e) {
    return scratchRollQuaternion = StaticQuaternion.fromAxisAngle(Cartesian3.UNIT_X, t.roll, scratchHPRQuaternion$1), scratchPitchQuaternion = StaticQuaternion.fromAxisAngle(Cartesian3.UNIT_Y, -t.pitch, e), e = StaticQuaternion.multiply(scratchPitchQuaternion, scratchRollQuaternion, scratchPitchQuaternion), scratchHeadingQuaternion = StaticQuaternion.fromAxisAngle(Cartesian3.UNIT_Z, -t.heading, scratchHPRQuaternion$1), StaticQuaternion.multiply(scratchHeadingQuaternion, e, e);
  }
}
class StaticMatrix4 {
  static clone(t, e) {
    return e.copy(t), e;
  }
  static inverseTransformation(t, e) {
    return e.copy(t).invert(), e;
  }
  static multiplyByPoint(t, e, i) {
    const s = t.elements, r = e.x, a = e.y, n = e.z, o = s[0] * r + s[4] * a + s[8] * n + s[12], h = s[1] * r + s[5] * a + s[9] * n + s[13], c = s[2] * r + s[6] * a + s[10] * n + s[14];
    return i.x = o, i.y = h, i.z = c, i;
  }
  static multiplyByPointAsVector(t, e, i) {
    const s = t.elements, r = e.x, a = e.y, n = e.z, o = s[0] * r + s[4] * a + s[8] * n, h = s[1] * r + s[5] * a + s[9] * n, c = s[2] * r + s[6] * a + s[10] * n;
    return i.x = o, i.y = h, i.z = c, i;
  }
  static computeViewportTransformation(t, e, i, s) {
    defined$1(s) || (s = new Matrix4()), t = defaultValue(t, defaultValue.EMPTY_OBJECT);
    const r = defaultValue(t.x, 0), a = defaultValue(t.y, 0), n = defaultValue(t.width, 0), o = defaultValue(t.height, 0);
    e = defaultValue(e, 0);
    const h = 0.5 * n, c = 0.5 * o, l = 0.5 * ((i = defaultValue(i, 1)) - e), u = h, d = c, m = l, _ = r + h, f = a + c, p = e + l, x = s.elements;
    return x[0] = u, x[1] = 0, x[2] = 0, x[3] = 0, x[4] = 0, x[5] = d, x[6] = 0, x[7] = 0, x[8] = 0, x[9] = 0, x[10] = m, x[11] = 0, x[12] = _, x[13] = f, x[14] = p, x[15] = 1, s;
  }
  static equals(t, e) {
    return t.equals(e);
  }
  static multiplyByVector(t, e, i) {
    return i || (i = new Vector4()), i.copy(e), i.applyMatrix4(t), i;
  }
  static getColumn(t, e, i) {
    const s = t.elements, r = 4 * e, a = s[r], n = s[r + 1], o = s[r + 2], h = s[r + 3];
    return i.x = a, i.y = n, i.z = o, i.w = h, i;
  }
  static fromTranslationQuaternionRotationScale(t, e, i, s) {
    s || (s = new Matrix4());
    const r = i.x, a = i.y, n = i.z, o = e.x * e.x, h = e.x * e.y, c = e.x * e.z, l = e.x * e.w, u = e.y * e.y, d = e.y * e.z, m = e.y * e.w, _ = e.z * e.z, f = e.z * e.w, p = e.w * e.w, x = o - u - _ + p, y = 2 * (h - f), M = 2 * (c + m), g = 2 * (h + f), w = -o + u - _ + p, P = 2 * (d - l), S = 2 * (c - m), C = 2 * (d + l), E = -o - u + _ + p, v = s.elements;
    return v[0] = x * r, v[1] = g * r, v[2] = S * r, v[3] = 0, v[4] = y * a, v[5] = w * a, v[6] = C * a, v[7] = 0, v[8] = M * n, v[9] = P * n, v[10] = E * n, v[11] = 0, v[12] = t.x, v[13] = t.y, v[14] = t.z, v[15] = 1, s;
  }
}
__publicField(StaticMatrix4, "IDENTITY", Object.freeze(new Matrix4()));
StaticMatrix4.ZERO = Object.freeze(new Matrix4(0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0));
const Transforms = {}, scratchHPRQuaternion = new Quaternion$1(), scratchScale$1 = new Vector3$1(1, 1, 1), scratchHPRMatrix4 = new Matrix4(), vectorProductLocalFrame = { up: { south: "east", north: "west", west: "south", east: "north" }, down: { south: "west", north: "east", west: "north", east: "south" }, south: { up: "west", down: "east", west: "down", east: "up" }, north: { up: "east", down: "west", west: "up", east: "down" }, west: { up: "north", down: "south", north: "down", south: "up" }, east: { up: "south", down: "north", north: "up", south: "down" } };
let degeneratePositionLocalFrame = { north: [-1, 0, 0], east: [0, 1, 0], up: [0, 0, 1], south: [1, 0, 0], west: [0, -1, 0], down: [0, 0, -1] }, localFrameToFixedFrameCache = {}, scratchCalculateCartesian = { east: new Vector3$1(), north: new Vector3$1(), up: new Vector3$1(), west: new Vector3$1(), south: new Vector3$1(), down: new Vector3$1() }, scratchFirstCartesian = new Vector3$1(), scratchSecondCartesian = new Vector3$1(), scratchThirdCartesian = new Vector3$1();
const defined = (t) => void 0 !== t, zeroVector3 = new Vector3$1(), mathSign = (t) => 0 === (t = +t) ? t : t > 0 ? 1 : -1, scratchN = new Vector3$1(), scratchK = new Vector3$1(), radianToEcef = function(t, e, i = 0, s) {
  const r = new Vector3$1(40680631590769, 40680631590769, 40408299984661445e-3), a = Math.cos(e);
  scratchN.x = a * Math.cos(t), scratchN.y = a * Math.sin(t), scratchN.z = Math.sin(e), scratchN.normalize(), scratchK.multiplyVectors(r, scratchN);
  const n = Math.sqrt(scratchN.dot(scratchK));
  return scratchK.divideScalar(n), scratchN.multiplyScalar(i), defined(s) || (s = new Vector3$1()), s.addVectors(scratchK, scratchN);
}, lnglatToEcef = (t, e, i = 0, s) => radianToEcef(t * Math.PI / 180, e * Math.PI / 180, i, s);
function Interval(t, e) {
  this.start = defaultValue(t, 0), this.stop = defaultValue(e, 0);
}
Transforms.lnglatToEcef = lnglatToEcef, Transforms.radianToEcef = radianToEcef, Transforms.localFrameToFixedFrameGenerator = function(t, e) {
  if (!vectorProductLocalFrame.hasOwnProperty(t) || !vectorProductLocalFrame[t].hasOwnProperty(e))
    throw new Error("firstAxis and secondAxis must be east, north, up, west, south or down.");
  let i, s = vectorProductLocalFrame[t][e], r = t + e;
  return defined(localFrameToFixedFrameCache[r]) ? i = localFrameToFixedFrameCache[r] : (i = function(i2, r2, a) {
    if (!defined(i2))
      throw new Error("origin is required.");
    if (defined(a) || (a = new Matrix4()), i2.equals(zeroVector3))
      scratchFirstCartesian.fromArray(degeneratePositionLocalFrame[t]), scratchSecondCartesian.fromArray(degeneratePositionLocalFrame[e]), scratchThirdCartesian.fromArray(degeneratePositionLocalFrame[s]);
    else if (Math.abs(i2.x) < 1e-14 && Math.abs(i2.y) < 1e-14) {
      let r3 = mathSign(i2.z);
      scratchFirstCartesian.fromArray(degeneratePositionLocalFrame[t]), "east" !== t && "west" !== t && scratchFirstCartesian.multiplyScalar(r3), scratchSecondCartesian.fromArray(degeneratePositionLocalFrame[e]), "east" !== e && "west" !== e && scratchSecondCartesian.multiplyScalar(r3), scratchThirdCartesian.fromArray(degeneratePositionLocalFrame[s]), "east" !== s && "west" !== s && scratchThirdCartesian.multiplyScalar(r3);
    } else {
      (r2 = r2 || Ellipsoid.WGS84).geodeticSurfaceNormal(i2, scratchCalculateCartesian.up);
      let a2 = scratchCalculateCartesian.up, n2 = scratchCalculateCartesian.east;
      n2.x = -i2.y, n2.y = i2.x, n2.z = 0, scratchCalculateCartesian.east.copy(n2).normalize(), scratchCalculateCartesian.north.crossVectors(a2, n2), scratchCalculateCartesian.down.copy(scratchCalculateCartesian.up).multiplyScalar(-1), scratchCalculateCartesian.west.copy(scratchCalculateCartesian.east).multiplyScalar(-1), scratchCalculateCartesian.south.copy(scratchCalculateCartesian.north).multiplyScalar(-1), scratchFirstCartesian = scratchCalculateCartesian[t], scratchSecondCartesian = scratchCalculateCartesian[e], scratchThirdCartesian = scratchCalculateCartesian[s];
    }
    const n = a.elements;
    return n[0] = scratchFirstCartesian.x, n[1] = scratchFirstCartesian.y, n[2] = scratchFirstCartesian.z, n[3] = 0, n[4] = scratchSecondCartesian.x, n[5] = scratchSecondCartesian.y, n[6] = scratchSecondCartesian.z, n[7] = 0, n[8] = scratchThirdCartesian.x, n[9] = scratchThirdCartesian.y, n[10] = scratchThirdCartesian.z, n[11] = 0, n[12] = i2.x, n[13] = i2.y, n[14] = i2.z, n[15] = 1, a;
  }, localFrameToFixedFrameCache[r] = i), i;
}, Transforms.eastNorthUpToFixedFrame = Transforms.localFrameToFixedFrameGenerator("east", "north"), Transforms.headingPitchRollToFixedFrame = function(t, e, i, s, r) {
  s = s || Transforms.eastNorthUpToFixedFrame;
  const a = StaticQuaternion.fromHeadingPitchRoll(e, scratchHPRQuaternion), n = StaticMatrix4.fromTranslationQuaternionRotationScale(Cartesian3.ZERO, a, scratchScale$1, scratchHPRMatrix4);
  return (r = s(t, i, r)).multiply(n);
}, Transforms.northEastDownToFixedFrame = Transforms.localFrameToFixedFrameGenerator("north", "east"), Transforms.northUpEastToFixedFrame = Transforms.localFrameToFixedFrameGenerator("north", "up"), Transforms.northWestUpToFixedFrame = Transforms.localFrameToFixedFrameGenerator("north", "west");
class StaticMatrix3 {
  static fromQuaternion(t, e) {
    const i = t.x * t.x, s = t.x * t.y, r = t.x * t.z, a = t.x * t.w, n = t.y * t.y, o = t.y * t.z, h = t.y * t.w, c = t.z * t.z, l = t.z * t.w, u = t.w * t.w, d = i - n - c + u, m = 2 * (s - l), _ = 2 * (r + h), f = 2 * (s + l), p = -i + n - c + u, x = 2 * (o - a), y = 2 * (r - h), M = 2 * (o + a), g = -i - n + c + u;
    return e || (e = new Matrix3()), e.set(d, m, _, f, p, x, y, M, g), e;
  }
  static getColumn(t, e, i) {
    const s = t.elements, r = 3 * e, a = s[r], n = s[r + 1], o = s[r + 2];
    return i.x = a, i.y = n, i.z = o, i;
  }
  static multiplyByVector(t, e, i) {
    return i || (i = new Vector3$1()), i.copy(e), i.applyMatrix3(t), i;
  }
  static multiplyByScale(t, e, i) {
    i || (i = new Matrix3());
    const s = i.elements, r = t.elements;
    return s[0] = r[0] * e.x, s[1] = r[1] * e.x, s[2] = r[2] * e.x, s[3] = r[3] * e.y, s[4] = r[4] * e.y, s[5] = r[5] * e.y, s[6] = r[6] * e.z, s[7] = r[7] * e.z, s[8] = r[8] * e.z, i;
  }
  static transpose(t, e) {
    return e || (e = new Matrix3()), e.copy(t).transpose(), e;
  }
  static fromScale(t, e) {
    e || (e = new Matrix3());
    const i = e.elements;
    return i[0] = t.x, i[1] = 0, i[2] = 0, i[3] = 0, i[4] = t.y, i[5] = 0, i[6] = 0, i[7] = 0, i[8] = t.z, e;
  }
  static multiply(t, e, i) {
    i || (i = new Matrix3());
    const s = t.elements, r = e.elements, a = i.elements, n = s[0], o = s[3], h = s[6], c = s[1], l = s[4], u = s[7], d = s[2], m = s[5], _ = s[8], f = r[0], p = r[3], x = r[6], y = r[1], M = r[4], g = r[7], w = r[2], P = r[5], S = r[8];
    return a[0] = n * f + o * y + h * w, a[3] = n * p + o * M + h * P, a[6] = n * x + o * g + h * S, a[1] = c * f + l * y + u * w, a[4] = c * p + l * M + u * P, a[7] = c * x + l * g + u * S, a[2] = d * f + m * y + _ * w, a[5] = d * p + m * M + _ * P, a[8] = d * x + m * g + _ * S, i;
  }
  static clone(t, e) {
    if (defined$1(t))
      return defined$1(e) ? (e.clone(t), e) : new Matrix3(t[0], t[3], t[6], t[1], t[4], t[7], t[2], t[5], t[8]);
  }
  static setColumn(t, e, i, s) {
    const r = (s = StaticMatrix3.clone(t, s)).elements, a = 3 * e;
    return r[a] = i.x, r[a + 1] = i.y, r[a + 2] = i.z, s;
  }
}
StaticMatrix3.ZERO = Matrix3.ZERO = Object.freeze(new Matrix3(0, 0, 0, 0, 0, 0, 0, 0, 0)), StaticMatrix3.COLUMN0ROW0 = 0, StaticMatrix3.COLUMN0ROW1 = 1, StaticMatrix3.COLUMN0ROW2 = 2, StaticMatrix3.COLUMN1ROW0 = 3, StaticMatrix3.COLUMN1ROW1 = 4, StaticMatrix3.COLUMN1ROW2 = 5, StaticMatrix3.COLUMN2ROW0 = 6, StaticMatrix3.COLUMN2ROW1 = 7, StaticMatrix3.COLUMN2ROW2 = 8;
var QuadraticRealPolynomial = {};
function addWithCancellationCheck$1(t, e, i) {
  var s = t + e;
  return CesiumMath.sign(t) !== CesiumMath.sign(e) && Math.abs(s / Math.max(Math.abs(t), Math.abs(e))) < i ? 0 : s;
}
QuadraticRealPolynomial.computeDiscriminant = function(t, e, i) {
  if ("number" != typeof t)
    throw new DeveloperError("a is a required number.");
  if ("number" != typeof e)
    throw new DeveloperError("b is a required number.");
  if ("number" != typeof i)
    throw new DeveloperError("c is a required number.");
  return e * e - 4 * t * i;
}, QuadraticRealPolynomial.computeRealRoots = function(t, e, i) {
  if ("number" != typeof t)
    throw new DeveloperError("a is a required number.");
  if ("number" != typeof e)
    throw new DeveloperError("b is a required number.");
  if ("number" != typeof i)
    throw new DeveloperError("c is a required number.");
  var s;
  if (0 === t)
    return 0 === e ? [] : [-i / e];
  if (0 === e) {
    if (0 === i)
      return [0, 0];
    var r = Math.abs(i), a = Math.abs(t);
    if (r < a && r / a < CesiumMath.EPSILON14)
      return [0, 0];
    if (r > a && a / r < CesiumMath.EPSILON14)
      return [];
    if ((s = -i / t) < 0)
      return [];
    var n = Math.sqrt(s);
    return [-n, n];
  }
  if (0 === i)
    return (s = -e / t) < 0 ? [s, 0] : [0, s];
  var o = addWithCancellationCheck$1(e * e, -(4 * t * i), CesiumMath.EPSILON14);
  if (o < 0)
    return [];
  var h = -0.5 * addWithCancellationCheck$1(e, CesiumMath.sign(e) * Math.sqrt(o), CesiumMath.EPSILON14);
  return e > 0 ? [h / t, i / h] : [i / h, h / t];
};
var CubicRealPolynomial = {};
function computeRealRoots(t, e, i, s) {
  var r, a, n = t, o = e / 3, h = i / 3, c = s, l = n * h, u = o * c, d = o * o, m = h * h, _ = n * h - d, f = n * c - o * h, p = o * c - m, x = 4 * _ * p - f * f;
  if (x < 0) {
    var y, M, g;
    d * u >= l * m ? (y = n, M = _, g = -2 * o * _ + n * f) : (y = c, M = p, g = -c * f + 2 * h * p);
    var w = -(g < 0 ? -1 : 1) * Math.abs(y) * Math.sqrt(-x), P = (a = -g + w) / 2, S = P < 0 ? -Math.pow(-P, 1 / 3) : Math.pow(P, 1 / 3), C = a === w ? -S : -M / S;
    return r = M <= 0 ? S + C : -g / (S * S + C * C + M), d * u >= l * m ? [(r - o) / n] : [-c / (r + h)];
  }
  var E = _, v = -2 * o * _ + n * f, b = p, z = -c * f + 2 * h * p, T = Math.sqrt(x), A2 = Math.sqrt(3) / 2, O = Math.abs(Math.atan2(n * T, -v) / 3);
  r = 2 * Math.sqrt(-E);
  var G = Math.cos(O);
  a = r * G;
  var N = r * (-G / 2 - A2 * Math.sin(O)), I = a + N > 2 * o ? a - o : N - o, R = n, $ = I / R;
  O = Math.abs(Math.atan2(c * T, -z) / 3);
  var V = -c, L = (a = (r = 2 * Math.sqrt(-b)) * (G = Math.cos(O))) + (N = r * (-G / 2 - A2 * Math.sin(O))) < 2 * h ? a + h : N + h, B = V / L, q = -I * L - R * V, j = (h * q - o * (I * V)) / (-o * q + h * (R * L));
  return $ <= j ? $ <= B ? j <= B ? [$, j, B] : [$, B, j] : [B, $, j] : $ <= B ? [j, $, B] : j <= B ? [j, B, $] : [B, j, $];
}
CubicRealPolynomial.computeDiscriminant = function(t, e, i, s) {
  if ("number" != typeof t)
    throw new DeveloperError("a is a required number.");
  if ("number" != typeof e)
    throw new DeveloperError("b is a required number.");
  if ("number" != typeof i)
    throw new DeveloperError("c is a required number.");
  if ("number" != typeof s)
    throw new DeveloperError("d is a required number.");
  var r = e * e, a = i * i;
  return 18 * t * e * i * s + r * a - 27 * (t * t) * (s * s) - 4 * (t * a * i + r * e * s);
}, CubicRealPolynomial.computeRealRoots = function(t, e, i, s) {
  if ("number" != typeof t)
    throw new DeveloperError("a is a required number.");
  if ("number" != typeof e)
    throw new DeveloperError("b is a required number.");
  if ("number" != typeof i)
    throw new DeveloperError("c is a required number.");
  if ("number" != typeof s)
    throw new DeveloperError("d is a required number.");
  var r, a;
  if (0 === t)
    return QuadraticRealPolynomial.computeRealRoots(e, i, s);
  if (0 === e) {
    if (0 === i) {
      if (0 === s)
        return [0, 0, 0];
      var n = (a = -s / t) < 0 ? -Math.pow(-a, 1 / 3) : Math.pow(a, 1 / 3);
      return [n, n, n];
    }
    return 0 === s ? 0 === (r = QuadraticRealPolynomial.computeRealRoots(t, 0, i)).Length ? [0] : [r[0], 0, r[1]] : computeRealRoots(t, 0, i, s);
  }
  return 0 === i ? 0 === s ? (a = -e / t) < 0 ? [a, 0, 0] : [0, 0, a] : computeRealRoots(t, e, 0, s) : 0 === s ? 0 === (r = QuadraticRealPolynomial.computeRealRoots(t, e, i)).length ? [0] : r[1] <= 0 ? [r[0], r[1], 0] : r[0] >= 0 ? [0, r[0], r[1]] : [r[0], 0, r[1]] : computeRealRoots(t, e, i, s);
};
var QuarticRealPolynomial = {};
function original(t, e, i, s) {
  var r = t * t, a = e - 3 * r / 8, n = i - e * t / 2 + r * t / 8, o = s - i * t / 4 + e * r / 16 - 3 * r * r / 256, h = CubicRealPolynomial.computeRealRoots(1, 2 * a, a * a - 4 * o, -n * n);
  if (h.length > 0) {
    var c = -t / 4, l = h[h.length - 1];
    if (Math.abs(l) < CesiumMath.EPSILON14) {
      var u = QuadraticRealPolynomial.computeRealRoots(1, a, o);
      if (2 === u.length) {
        var d, m = u[0], _ = u[1];
        if (m >= 0 && _ >= 0) {
          var f = Math.sqrt(m), p = Math.sqrt(_);
          return [c - p, c - f, c + f, c + p];
        }
        if (m >= 0 && _ < 0)
          return [c - (d = Math.sqrt(m)), c + d];
        if (m < 0 && _ >= 0)
          return [c - (d = Math.sqrt(_)), c + d];
      }
      return [];
    }
    if (l > 0) {
      var x = Math.sqrt(l), y = (a + l - n / x) / 2, M = (a + l + n / x) / 2, g = QuadraticRealPolynomial.computeRealRoots(1, x, y), w = QuadraticRealPolynomial.computeRealRoots(1, -x, M);
      return 0 !== g.length ? (g[0] += c, g[1] += c, 0 !== w.length ? (w[0] += c, w[1] += c, g[1] <= w[0] ? [g[0], g[1], w[0], w[1]] : w[1] <= g[0] ? [w[0], w[1], g[0], g[1]] : g[0] >= w[0] && g[1] <= w[1] ? [w[0], g[0], g[1], w[1]] : w[0] >= g[0] && w[1] <= g[1] ? [g[0], w[0], w[1], g[1]] : g[0] > w[0] && g[0] < w[1] ? [w[0], g[0], w[1], g[1]] : [g[0], w[0], g[1], w[1]]) : g) : 0 !== w.length ? (w[0] += c, w[1] += c, w) : [];
    }
  }
  return [];
}
function neumark(t, e, i, s) {
  var r = t * t, a = -2 * e, n = i * t + e * e - 4 * s, o = r * s - i * e * t + i * i, h = CubicRealPolynomial.computeRealRoots(1, a, n, o);
  if (h.length > 0) {
    var c, l, u, d, m, _, f = h[0], p = e - f, x = p * p, y = t / 2, M = p / 2, g = x - 4 * s, w = x + 4 * Math.abs(s), P = r - 4 * f, S = r + 4 * Math.abs(f);
    if (f < 0 || g * S < P * w) {
      var C = Math.sqrt(P);
      c = C / 2, l = 0 === C ? 0 : (t * M - i) / C;
    } else {
      var E = Math.sqrt(g);
      c = 0 === E ? 0 : (t * M - i) / E, l = E / 2;
    }
    0 === y && 0 === c ? (u = 0, d = 0) : CesiumMath.sign(y) === CesiumMath.sign(c) ? d = f / (u = y + c) : u = f / (d = y - c), 0 === M && 0 === l ? (m = 0, _ = 0) : CesiumMath.sign(M) === CesiumMath.sign(l) ? _ = s / (m = M + l) : m = s / (_ = M - l);
    var v = QuadraticRealPolynomial.computeRealRoots(1, u, m), b = QuadraticRealPolynomial.computeRealRoots(1, d, _);
    if (0 !== v.length)
      return 0 !== b.length ? v[1] <= b[0] ? [v[0], v[1], b[0], b[1]] : b[1] <= v[0] ? [b[0], b[1], v[0], v[1]] : v[0] >= b[0] && v[1] <= b[1] ? [b[0], v[0], v[1], b[1]] : b[0] >= v[0] && b[1] <= v[1] ? [v[0], b[0], b[1], v[1]] : v[0] > b[0] && v[0] < b[1] ? [b[0], v[0], b[1], v[1]] : [v[0], b[0], v[1], b[1]] : v;
    if (0 !== b.length)
      return b;
  }
  return [];
}
QuarticRealPolynomial.computeDiscriminant = function(t, e, i, s, r) {
  if ("number" != typeof t)
    throw new DeveloperError("a is a required number.");
  if ("number" != typeof e)
    throw new DeveloperError("b is a required number.");
  if ("number" != typeof i)
    throw new DeveloperError("c is a required number.");
  if ("number" != typeof s)
    throw new DeveloperError("d is a required number.");
  if ("number" != typeof r)
    throw new DeveloperError("e is a required number.");
  var a = t * t, n = e * e, o = n * e, h = i * i, c = h * i, l = s * s, u = l * s, d = r * r;
  return n * h * l - 4 * o * u - 4 * t * c * l + 18 * t * e * i * u - 27 * a * l * l + 256 * (a * t) * (d * r) + r * (18 * o * i * s - 4 * n * c + 16 * t * h * h - 80 * t * e * h * s - 6 * t * n * l + 144 * a * i * l) + d * (144 * t * n * i - 27 * n * n - 128 * a * h - 192 * a * e * s);
}, QuarticRealPolynomial.computeRealRoots = function(t, e, i, s, r) {
  if ("number" != typeof t)
    throw new DeveloperError("a is a required number.");
  if ("number" != typeof e)
    throw new DeveloperError("b is a required number.");
  if ("number" != typeof i)
    throw new DeveloperError("c is a required number.");
  if ("number" != typeof s)
    throw new DeveloperError("d is a required number.");
  if ("number" != typeof r)
    throw new DeveloperError("e is a required number.");
  if (Math.abs(t) < CesiumMath.EPSILON15)
    return CubicRealPolynomial.computeRealRoots(e, i, s, r);
  var a = e / t, n = i / t, o = s / t, h = r / t, c = a < 0 ? 1 : 0;
  switch (c += n < 0 ? c + 1 : c, c += o < 0 ? c + 1 : c, c += h < 0 ? c + 1 : c) {
    case 0:
    case 3:
    case 4:
    case 6:
    case 7:
    case 9:
    case 10:
    case 12:
    case 13:
    case 14:
    case 15:
      return original(a, n, o, h);
    case 1:
    case 2:
    case 5:
    case 8:
    case 11:
      return neumark(a, n, o, h);
    default:
      return;
  }
};
var IntersectionTests = { rayPlane: function(t, e, i) {
  if (!defined$1(t))
    throw new DeveloperError("ray is required.");
  if (!defined$1(e))
    throw new DeveloperError("plane is required.");
  defined$1(i) || (i = new Vector3$1());
  var s = t.origin, r = t.direction, a = e.normal, n = Cartesian3.dot(a, r);
  if (!(Math.abs(n) < CesiumMath.EPSILON15)) {
    var o = (-e.constant - Cartesian3.dot(a, s)) / n;
    if (!(o < 0))
      return i = Cartesian3.multiplyByScalar(r, o, i), Cartesian3.add(s, i, i);
  }
} }, scratchEdge0 = new Vector3$1(), scratchEdge1 = new Vector3$1(), scratchPVec = new Vector3$1(), scratchTVec = new Vector3$1(), scratchQVec = new Vector3$1();
IntersectionTests.rayTriangleParametric = function(t, e, i, s, r) {
  if (!defined$1(t))
    throw new DeveloperError("ray is required.");
  if (!defined$1(e))
    throw new DeveloperError("p0 is required.");
  if (!defined$1(i))
    throw new DeveloperError("p1 is required.");
  if (!defined$1(s))
    throw new DeveloperError("p2 is required.");
  r = defaultValue(r, false);
  var a, n, o, h, c, l = t.origin, u = t.direction, d = Cartesian3.subtract(i, e, scratchEdge0), m = Cartesian3.subtract(s, e, scratchEdge1), _ = Cartesian3.cross(u, m, scratchPVec), f = Cartesian3.dot(d, _);
  if (r) {
    if (f < CesiumMath.EPSILON6)
      return;
    if (a = Cartesian3.subtract(l, e, scratchTVec), (o = Cartesian3.dot(a, _)) < 0 || o > f)
      return;
    if (n = Cartesian3.cross(a, d, scratchQVec), (h = Cartesian3.dot(u, n)) < 0 || o + h > f)
      return;
    c = Cartesian3.dot(m, n) / f;
  } else {
    if (Math.abs(f) < CesiumMath.EPSILON6)
      return;
    var p = 1 / f;
    if (a = Cartesian3.subtract(l, e, scratchTVec), (o = Cartesian3.dot(a, _) * p) < 0 || o > 1)
      return;
    if (n = Cartesian3.cross(a, d, scratchQVec), (h = Cartesian3.dot(u, n) * p) < 0 || o + h > 1)
      return;
    c = Cartesian3.dot(m, n) * p;
  }
  return c;
}, IntersectionTests.rayTriangle = function(t, e, i, s, r, a) {
  var n = IntersectionTests.rayTriangleParametric(t, e, i, s, r);
  if (defined$1(n) && !(n < 0))
    return defined$1(a) || (a = new Vector3$1()), Cartesian3.multiplyByScalar(t.direction, n, a), Cartesian3.add(t.origin, a, a);
};
var scratchLineSegmentTriangleRay = new Ray();
function solveQuadratic(t, e, i, s) {
  var r = e * e - 4 * t * i;
  if (!(r < 0)) {
    if (r > 0) {
      var a = 1 / (2 * t), n = Math.sqrt(r), o = (-e + n) * a, h = (-e - n) * a;
      return o < h ? (s.root0 = o, s.root1 = h) : (s.root0 = h, s.root1 = o), s;
    }
    var c = -e / (2 * t);
    if (0 !== c)
      return s.root0 = s.root1 = c, s;
  }
}
IntersectionTests.lineSegmentTriangle = function(t, e, i, s, r, a, n) {
  if (!defined$1(t))
    throw new DeveloperError("v0 is required.");
  if (!defined$1(e))
    throw new DeveloperError("v1 is required.");
  if (!defined$1(i))
    throw new DeveloperError("p0 is required.");
  if (!defined$1(s))
    throw new DeveloperError("p1 is required.");
  if (!defined$1(r))
    throw new DeveloperError("p2 is required.");
  var o = scratchLineSegmentTriangleRay;
  Cartesian3.clone(t, o.origin), Cartesian3.subtract(e, t, o.direction), Cartesian3.normalize(o.direction, o.direction);
  var h = IntersectionTests.rayTriangleParametric(o, i, s, r, a);
  if (!(!defined$1(h) || h < 0 || h > Cartesian3.distance(t, e)))
    return defined$1(n) || (n = new Vector3$1()), Cartesian3.multiplyByScalar(o.direction, h, n), Cartesian3.add(o.origin, n, n);
};
var raySphereRoots = { root0: 0, root1: 0 };
function raySphere(t, e, i) {
  defined$1(i) || (i = new Interval());
  var s = t.origin, r = t.direction, a = e.center, n = e.radius * e.radius, o = Cartesian3.subtract(s, a, scratchPVec), h = solveQuadratic(Cartesian3.dot(r, r), 2 * Cartesian3.dot(r, o), Cartesian3.magnitudeSquared(o) - n, raySphereRoots);
  if (defined$1(h))
    return i.start = h.root0, i.stop = h.root1, i;
}
IntersectionTests.raySphere = function(t, e, i) {
  if (!defined$1(t))
    throw new DeveloperError("ray is required.");
  if (!defined$1(e))
    throw new DeveloperError("sphere is required.");
  if (defined$1(i = raySphere(t, e, i)) && !(i.stop < 0))
    return i.start = Math.max(i.start, 0), i;
};
var scratchLineSegmentRay = new Ray();
IntersectionTests.lineSegmentSphere = function(t, e, i, s) {
  if (!defined$1(t))
    throw new DeveloperError("p0 is required.");
  if (!defined$1(e))
    throw new DeveloperError("p1 is required.");
  if (!defined$1(i))
    throw new DeveloperError("sphere is required.");
  var r = scratchLineSegmentRay;
  Cartesian3.clone(t, r.origin);
  var a = Cartesian3.subtract(e, t, r.direction), n = Cartesian3.magnitude(a);
  if (Cartesian3.normalize(a, a), !(!defined$1(s = raySphere(r, i, s)) || s.stop < 0 || s.start > n))
    return s.start = Math.max(s.start, 0), s.stop = Math.min(s.stop, n), s;
};
var scratchQ = new Vector3$1(), scratchW = new Vector3$1();
function addWithCancellationCheck(t, e, i) {
  var s = t + e;
  return CesiumMath.sign(t) !== CesiumMath.sign(e) && Math.abs(s / Math.max(Math.abs(t), Math.abs(e))) < i ? 0 : s;
}
function quadraticVectorExpression(t, e, i, s, r) {
  var a, n = s * s, o = r * r, h = (t[StaticMatrix3.COLUMN1ROW1] - t[StaticMatrix3.COLUMN2ROW2]) * o, c = r * (s * addWithCancellationCheck(t[StaticMatrix3.COLUMN1ROW0], t[StaticMatrix3.COLUMN0ROW1], CesiumMath.EPSILON15) + e.y), l = t[StaticMatrix3.COLUMN0ROW0] * n + t[StaticMatrix3.COLUMN2ROW2] * o + s * e.x + i, u = o * addWithCancellationCheck(t[StaticMatrix3.COLUMN2ROW1], t[StaticMatrix3.COLUMN1ROW2], CesiumMath.EPSILON15), d = r * (s * addWithCancellationCheck(t[StaticMatrix3.COLUMN2ROW0], t[StaticMatrix3.COLUMN0ROW2]) + e.z), m = [];
  if (0 === d && 0 === u) {
    if (0 === (a = QuadraticRealPolynomial.computeRealRoots(h, c, l)).length)
      return m;
    var _ = a[0], f = Math.sqrt(Math.max(1 - _ * _, 0));
    if (m.push(new Vector3$1(s, r * _, r * -f)), m.push(new Vector3$1(s, r * _, r * f)), 2 === a.length) {
      var p = a[1], x = Math.sqrt(Math.max(1 - p * p, 0));
      m.push(new Vector3$1(s, r * p, r * -x)), m.push(new Vector3$1(s, r * p, r * x));
    }
    return m;
  }
  var y = d * d, M = u * u, g = d * u, w = h * h + M, P = 2 * (c * h + g), S = 2 * l * h + c * c - M + y, C = 2 * (l * c - g), E = l * l - y;
  if (0 === w && 0 === P && 0 === S && 0 === C)
    return m;
  var v = (a = QuarticRealPolynomial.computeRealRoots(w, P, S, C, E)).length;
  if (0 === v)
    return m;
  for (var b = 0; b < v; ++b) {
    var z = a[b], T = z * z, A2 = Math.max(1 - T, 0), O = Math.sqrt(A2), G = (CesiumMath.sign(h) === CesiumMath.sign(l) ? addWithCancellationCheck(h * T + l, c * z, CesiumMath.EPSILON12) : CesiumMath.sign(l) === CesiumMath.sign(c * z) ? addWithCancellationCheck(h * T, c * z + l, CesiumMath.EPSILON12) : addWithCancellationCheck(h * T + c * z, l, CesiumMath.EPSILON12)) * addWithCancellationCheck(u * z, d, CesiumMath.EPSILON15);
    G < 0 ? m.push(new Vector3$1(s, r * z, r * O)) : G > 0 ? m.push(new Vector3$1(s, r * z, r * -O)) : 0 !== O ? (m.push(new Vector3$1(s, r * z, r * -O)), m.push(new Vector3$1(s, r * z, r * O)), ++b) : m.push(new Vector3$1(s, r * z, r * O));
  }
  return m;
}
IntersectionTests.rayEllipsoid = function(t, e) {
  if (!defined$1(t))
    throw new DeveloperError("ray is required.");
  if (!defined$1(e))
    throw new DeveloperError("ellipsoid is required.");
  var i, s, r, a, n, o = e.oneOverRadii, h = Cartesian3.multiplyComponents(o, t.origin, scratchQ), c = Cartesian3.multiplyComponents(o, t.direction, scratchW), l = Cartesian3.magnitudeSquared(h), u = Cartesian3.dot(h, c);
  if (l > 1) {
    if (u >= 0)
      return;
    var d = u * u;
    if (i = l - 1, d < (r = (s = Cartesian3.magnitudeSquared(c)) * i))
      return;
    if (d > r) {
      a = u * u - r;
      var m = (n = -u + Math.sqrt(a)) / s, _ = i / n;
      return m < _ ? new Interval(m, _) : { start: _, stop: m };
    }
    var f = Math.sqrt(i / s);
    return new Interval(f, f);
  }
  return l < 1 ? (i = l - 1, a = u * u - (r = (s = Cartesian3.magnitudeSquared(c)) * i), new Interval(0, (n = -u + Math.sqrt(a)) / s)) : u < 0 ? new Interval(0, -u / (s = Cartesian3.magnitudeSquared(c))) : void 0;
};
var firstAxisScratch = new Vector3$1(), secondAxisScratch = new Vector3$1(), thirdAxisScratch = new Vector3$1(), referenceScratch = new Vector3$1(), bCart = new Vector3$1(), bScratch = new Matrix3(), btScratch = new Matrix3(), diScratch = new Matrix3(), dScratch = new Matrix3(), cScratch = new Matrix3(), tempMatrix = new Matrix3(), aScratch = new Matrix3(), sScratch = new Vector3$1(), closestScratch = new Vector3$1(), surfPointScratch = new Vector3$1();
IntersectionTests.grazingAltitudeLocation = function(t, e) {
  if (!defined$1(t))
    throw new DeveloperError("ray is required.");
  if (!defined$1(e))
    throw new DeveloperError("ellipsoid is required.");
  var i = t.origin, s = t.direction;
  if (!Cartesian3.equals(i, Cartesian3.ZERO)) {
    var r = e.geodeticSurfaceNormal(i, firstAxisScratch);
    if (Cartesian3.dot(s, r) >= 0)
      return i;
  }
  var a = defined$1(this.rayEllipsoid(t, e)), n = e.transformPositionToScaledSpace(s, firstAxisScratch), o = Cartesian3.normalize(n, n), h = Cartesian3.mostOrthogonalAxis(n, referenceScratch), c = Cartesian3.normalize(Cartesian3.cross(h, o, secondAxisScratch), secondAxisScratch), l = Cartesian3.normalize(Cartesian3.cross(o, c, thirdAxisScratch), thirdAxisScratch), u = bScratch;
  u[0] = o.x, u[1] = o.y, u[2] = o.z, u[3] = c.x, u[4] = c.y, u[5] = c.z, u[6] = l.x, u[7] = l.y, u[8] = l.z;
  var d = StaticMatrix3.transpose(u, btScratch), m = StaticMatrix3.fromScale(e.radii, diScratch), _ = StaticMatrix3.fromScale(e.oneOverRadii, dScratch), f = cScratch;
  f[0] = 0, f[1] = -s.z, f[2] = s.y, f[3] = s.z, f[4] = 0, f[5] = -s.x, f[6] = -s.y, f[7] = s.x, f[8] = 0;
  var p, x, y = StaticMatrix3.multiply(StaticMatrix3.multiply(d, _, tempMatrix), f, tempMatrix), M = StaticMatrix3.multiply(StaticMatrix3.multiply(y, m, aScratch), u, aScratch), g = StaticMatrix3.multiplyByVector(y, i, bCart), w = quadraticVectorExpression(M, Cartesian3.negate(g, firstAxisScratch), 0, 0, 1), P = w.length;
  if (P > 0) {
    for (var S = Cartesian3.clone(Cartesian3.ZERO, closestScratch), C = Number.NEGATIVE_INFINITY, E = 0; E < P; ++E) {
      p = StaticMatrix3.multiplyByVector(m, StaticMatrix3.multiplyByVector(u, w[E], sScratch), sScratch);
      var v = Cartesian3.normalize(Cartesian3.subtract(p, i, referenceScratch), referenceScratch), b = Cartesian3.dot(v, s);
      b > C && (C = b, S = Cartesian3.clone(p, S));
    }
    var z = e.cartesianToCartographic(S, surfPointScratch);
    return C = CesiumMath.clamp(C, 0, 1), x = Cartesian3.magnitude(Cartesian3.subtract(S, i, referenceScratch)) * Math.sqrt(1 - C * C), x = a ? -x : x, z.z = x, e.cartographicToCartesian(z, new Vector3$1());
  }
};
var lineSegmentPlaneDifference = new Vector3$1();
IntersectionTests.lineSegmentPlane = function(t, e, i, s) {
  if (!defined$1(t))
    throw new DeveloperError("endPoint0 is required.");
  if (!defined$1(e))
    throw new DeveloperError("endPoint1 is required.");
  if (!defined$1(i))
    throw new DeveloperError("plane is required.");
  defined$1(s) || (s = new Vector3$1());
  var r = Cartesian3.subtract(e, t, lineSegmentPlaneDifference), a = i.normal, n = Cartesian3.dot(a, r);
  if (!(Math.abs(n) < CesiumMath.EPSILON6)) {
    var o = Cartesian3.dot(a, t), h = -(i.constant + o) / n;
    if (!(h < 0 || h > 1))
      return Cartesian3.multiplyByScalar(r, h, s), Cartesian3.add(t, s, s), s;
  }
}, IntersectionTests.trianglePlaneIntersection = function(t, e, i, s) {
  if (!(defined$1(t) && defined$1(e) && defined$1(i) && defined$1(s)))
    throw new DeveloperError("p0, p1, p2, and plane are required.");
  var r, a, n = s.normal, o = s.constant, h = Cartesian3.dot(n, t) + o < 0, c = Cartesian3.dot(n, e) + o < 0, l = Cartesian3.dot(n, i) + o < 0, u = 0;
  if (u += h ? 1 : 0, u += c ? 1 : 0, 1 !== (u += l ? 1 : 0) && 2 !== u || (r = new Vector3$1(), a = new Vector3$1()), 1 === u) {
    if (h)
      return IntersectionTests.lineSegmentPlane(t, e, s, r), IntersectionTests.lineSegmentPlane(t, i, s, a), { positions: [t, e, i, r, a], indices: [0, 3, 4, 1, 2, 4, 1, 4, 3] };
    if (c)
      return IntersectionTests.lineSegmentPlane(e, i, s, r), IntersectionTests.lineSegmentPlane(e, t, s, a), { positions: [t, e, i, r, a], indices: [1, 3, 4, 2, 0, 4, 2, 4, 3] };
    if (l)
      return IntersectionTests.lineSegmentPlane(i, t, s, r), IntersectionTests.lineSegmentPlane(i, e, s, a), { positions: [t, e, i, r, a], indices: [2, 3, 4, 0, 1, 4, 0, 4, 3] };
  } else if (2 === u) {
    if (!h)
      return IntersectionTests.lineSegmentPlane(e, t, s, r), IntersectionTests.lineSegmentPlane(i, t, s, a), { positions: [t, e, i, r, a], indices: [1, 2, 4, 1, 4, 3, 0, 3, 4] };
    if (!c)
      return IntersectionTests.lineSegmentPlane(i, e, s, r), IntersectionTests.lineSegmentPlane(t, e, s, a), { positions: [t, e, i, r, a], indices: [2, 0, 4, 2, 4, 3, 1, 3, 4] };
    if (!l)
      return IntersectionTests.lineSegmentPlane(t, i, s, r), IntersectionTests.lineSegmentPlane(e, i, s, a), { positions: [t, e, i, r, a], indices: [0, 1, 4, 0, 4, 3, 2, 3, 4] };
  }
};
class Cartesian4 {
  static clone(t, e) {
    return e || (e = new Vector4()), e.copy(t), e;
  }
  static fromElements(t, e, i, s, r) {
    return r || (r = new Vector4()), r.set(t, e, i, s), r;
  }
  static lerp(t, e, i, s) {
    return s || (s = new Vector4()), s.lerpVectors(t, e, i), s;
  }
  static equals(t, e) {
    return t.equals(e);
  }
  static normalize(t, e) {
    return t === e ? (t.normalize(), t) : (e.copy(t), e.normalize(), e);
  }
  static add(t, e, i) {
    return i || (i = new Vector4()), i.addVectors(t, e);
  }
  static multiplyByScalar(t, e, i) {
    return i || (i = new Vector4()), i.copy(t).multiplyScalar(e), i;
  }
  static subtract(t, e, i) {
    return i || (i = new Vector4()), i.subVectors(t, e), i;
  }
  static distance(t, e) {
    return t.distanceTo(e);
  }
}
__publicField(Cartesian4, "ZERO", new Vector4(0, 0, 0, 0));
__publicField(Cartesian4, "UNIT_W", Object.freeze(new Vector4(0, 0, 0, 1)));
const scratchNormal = new Vector3$1(), scratchCartesian$1 = new Vector3$1(), scratchInverseTranspose = new Matrix4(), scratchPlaneCartesian4 = new Vector4(0, 0, 0, 0), scratchTransformNormal = new Vector3$1();
class StaticPlane {
  static fromPointNormal(t, e, i) {
    return i || (i = new Plane()), i.setFromNormalAndCoplanarPoint(e, t), i;
  }
  static fromCartesian4(t, e) {
    const i = Cartesian3.fromCartesian4(t, scratchNormal), s = t.w;
    if (!CesiumMath.equalsEpsilon(Cartesian3.magnitude(i), 1, CesiumMath.EPSILON6))
      throw new Error("normal must be normalized.");
    return defined$1(e) ? (Cartesian3.clone(i, e.normal), e.constant = s, e) : new StaticPlane(i, s);
  }
  static getPointDistance(t, e) {
    return Cartesian3.dot(t.normal, e) + t.constant;
  }
  static projectPointOntoPlane(t, e, i) {
    defined$1(i) || (i = new Vector3$1());
    const s = StaticPlane.getPointDistance(t, e), r = Cartesian3.multiplyByScalar(t.normal, s, scratchCartesian$1);
    return Cartesian3.subtract(e, r, i);
  }
  static transform(t, e, i) {
    const s = t.normal, r = t.constant, a = Matrix4.inverseTranspose(e, scratchInverseTranspose);
    let n = Cartesian4.fromElements(s.x, s.y, s.z, r, scratchPlaneCartesian4);
    n = Matrix4.multiplyByVector(a, n, n);
    const o = Cartesian3.fromCartesian4(n, scratchTransformNormal);
    return n = Cartesian4.divideByScalar(n, Cartesian3.magnitude(o), n), Plane.fromCartesian4(n, i);
  }
  static clone(t, e) {
    return defined$1(e) ? (Cartesian3.clone(t.normal, e.normal), e.constant = t.constant, e) : new StaticPlane(t.normal, t.constant);
  }
  static equals(t, e) {
    return t.constant === e.constant && Cartesian3.equals(t.normal, e.normal);
  }
}
Plane.ORIGIN_XY_PLANE = Object.freeze(new StaticPlane(Cartesian3.UNIT_Z, 0)), Plane.ORIGIN_YZ_PLANE = Object.freeze(new StaticPlane(Cartesian3.UNIT_X, 0)), Plane.ORIGIN_ZX_PLANE = Object.freeze(new StaticPlane(Cartesian3.UNIT_Y, 0));
const scratchCart4 = new Vector4(0, 0, 0, 0), scratchProjectPointOntoPlaneRay$1 = new Ray(), scratchProjectPointOntoPlaneCartesian3$1 = new Vector3$1();
class EllipsoidTangentPlane {
  constructor(t, e) {
    if (!defined$1(t = (e = defaultValue(e, Ellipsoid.WGS84)).scaleToGeodeticSurface(t)))
      throw new DeveloperError("origin must not be at the center of the ellipsoid.");
    const i = Transforms.eastNorthUpToFixedFrame(t, e);
    this._ellipsoid = e, this._origin = t, this._xAxis = Cartesian3.fromCartesian4(StaticMatrix4.getColumn(i, 0, scratchCart4)), this._yAxis = Cartesian3.fromCartesian4(StaticMatrix4.getColumn(i, 1, scratchCart4));
    const s = Cartesian3.fromCartesian4(StaticMatrix4.getColumn(i, 2, scratchCart4));
    this._plane = StaticPlane.fromPointNormal(t, s);
  }
  static fromPoints(t, e) {
    let i = t[0].x, s = t[0].y, r = t[0].z, a = t[0].x, n = t[0].y, o = t[0].z;
    for (let e2 = 0; e2 < t.length; e2++) {
      const h2 = t[e2], c2 = h2.x, l2 = h2.y, u2 = h2.z;
      i = Math.min(c2, i), a = Math.max(c2, a), s = Math.min(l2, s), n = Math.max(l2, n), r = Math.min(u2, r), o = Math.max(u2, o);
    }
    const h = new Vector3$1(i, s, r), c = new Vector3$1(a, n, o), l = new Box3(h, c);
    let u = new Vector3$1();
    return u = l.getCenter(u), new EllipsoidTangentPlane(u, e);
  }
  projectPointToNearestOnPlane(t, e) {
    defined$1(e) || (e = new Cartesian2());
    const i = scratchProjectPointOntoPlaneRay$1;
    i.origin = t, Cartesian3.clone(this._plane.normal, i.direction);
    let s = IntersectionTests.rayPlane(i, this._plane, scratchProjectPointOntoPlaneCartesian3$1);
    if (defined$1(s) || (Cartesian3.negate(i.direction, i.direction), s = IntersectionTests.rayPlane(i, this._plane, scratchProjectPointOntoPlaneCartesian3$1)), defined$1(s)) {
      const t2 = Cartesian3.subtract(s, this._origin, s), i2 = Cartesian3.dot(this._xAxis, t2), r = Cartesian3.dot(this._yAxis, t2);
      return defined$1(e) ? (e.x = i2, e.y = r, e) : new Vector2(i2, r);
    }
  }
  projectPointsOntoPlane(t, e) {
    defined$1(e) || (e = []);
    let i = 0;
    const s = t.length;
    for (let r = 0; r < s; r++) {
      const s2 = this.projectPointOntoPlane(t[r], e[i]);
      s2 && (e[i] = s2, i++);
    }
    return e.length = i, e;
  }
  projectPointOntoPlane(t, e) {
    const i = scratchProjectPointOntoPlaneRay$1;
    i.origin = t, Cartesian3.normalize(t, i.direction);
    let s = IntersectionTests.rayPlane(i, this._plane, scratchProjectPointOntoPlaneCartesian3$1);
    if (defined$1(s) || (Cartesian3.negate(i.direction, i.direction), s = IntersectionTests.rayPlane(i, this._plane, scratchProjectPointOntoPlaneCartesian3$1)), defined$1(s)) {
      const t2 = Cartesian3.subtract(s, this._origin, s), i2 = Cartesian3.dot(this._xAxis, t2), r = Cartesian3.dot(this._yAxis, t2);
      return defined$1(e) ? (e.x = i2, e.y = r, e) : new Vector2(i2, r);
    }
  }
  get ellipsoid() {
    return this._ellipsoid;
  }
  get origin() {
    return this._origin;
  }
  get plane() {
    return this._plane;
  }
  get xAxis() {
    return this._xAxis;
  }
  get yAxis() {
    return this._yAxis;
  }
  get zAxis() {
    return this._plane.normal;
  }
}
const scratchCartographic = new Vector3$1(), scratchCartesian = new Vector3$1(), scratchProjectPointOntoPlaneRay = new Ray(), scratchProjectPointOntoPlaneRayDirection = new Vector3$1(), scratchProjectPointOntoPlaneCartesian3 = new Vector3$1();
const _Stereographic = class {
  constructor(t, e) {
    this.position = t, this.position || (t = new Vector2()), this.tangentPlane = e, this.tangentPlane || (this.tangentPlane = _Stereographic.NORTH_POLE_TANGENT_PLANE);
  }
  getLatitude(t) {
    t || (t = Ellipsoid.WGS84), scratchCartographic.x = this.longitude, scratchCartographic.y = this.conformalLatitude, scratchCartographic.z = 0;
    const e = this.ellipsoid.cartographicToCartesian(scratchCartographic, scratchCartesian);
    return t.cartesianToCartographic(e, scratchCartographic), scratchCartographic.y;
  }
  static fromCartesian(t, e) {
    const i = t.z < 0 ? -1 : 1;
    let s = _Stereographic.NORTH_POLE_TANGENT_PLANE, r = _Stereographic.SOUTH_POLE;
    i < 0 && (s = _Stereographic.SOUTH_POLE_TANGENT_PLANE, r = _Stereographic.NORTH_POLE);
    const a = scratchProjectPointOntoPlaneRay;
    a.origin = s.ellipsoid.scaleToGeocentricSurface(t, a.origin), a.direction = Cartesian3.subtract(a.origin, r, scratchProjectPointOntoPlaneRayDirection), Cartesian3.normalize(a.direction, a.direction);
    const n = IntersectionTests.rayPlane(a, s.plane, scratchProjectPointOntoPlaneCartesian3), o = Cartesian3.subtract(n, r, n), h = Cartesian3.dot(s.xAxis, o), c = i * Cartesian3.dot(s.yAxis, o);
    return e ? (e.position = new Vector2(h, c), e.tangentPlane = s, e) : new _Stereographic(new Vector2(h, c), s);
  }
  clone(t) {
    return _Stereographic.clone(this, t);
  }
  static clone(t, e) {
    if (t)
      return e ? (e.position = t.position, e.tangentPlane = t.tangentPlane, e) : new _Stereographic(t.position, t.tangentPlane);
  }
  get ellipsoid() {
    return this.tangentPlane.ellipsoid;
  }
  get x() {
    return this.position.x;
  }
  get y() {
    return this.position.y;
  }
  get conformalLatitude() {
    const t = this.position.length(), e = 2 * this.ellipsoid.maximumRadius;
    return this.tangentPlane.plane.normal.z * (CesiumMath.PI_OVER_TWO - 2 * Math.atan2(t, e));
  }
  get longitude() {
    let t = CesiumMath.PI_OVER_TWO + Math.atan2(this.y, this.x);
    return t > Math.PI && (t -= CesiumMath.TWO_PI), t;
  }
};
let Stereographic = _Stereographic;
__publicField(Stereographic, "HALF_UNIT_SPHERE", Object.freeze(new Ellipsoid(0.5, 0.5, 0.5)));
__publicField(Stereographic, "NORTH_POLE", Object.freeze(new Vector3$1(0, 0, 0.5)));
__publicField(Stereographic, "SOUTH_POLE", Object.freeze(new Vector3$1(0, 0, -0.5)));
__publicField(Stereographic, "NORTH_POLE_TANGENT_PLANE", Object.freeze(new EllipsoidTangentPlane(_Stereographic.NORTH_POLE, _Stereographic.HALF_UNIT_SPHERE)));
__publicField(Stereographic, "SOUTH_POLE_TANGENT_PLANE", Object.freeze(new EllipsoidTangentPlane(_Stereographic.SOUTH_POLE, _Stereographic.HALF_UNIT_SPHERE)));
function clamp(t, e, i) {
  return Math.max(e, Math.min(i, t));
}
new Stereographic(), new Stereographic(), new Vector2(), new Vector2(), new Stereographic(), new Vector2(), Object.freeze({}), Object.freeze(new Matrix4()), new Vector3$1(), new Vector3$1(), new Vector3$1(), new Vector3$1(), new Vector3$1(), new Vector3$1(), new Vector3$1(), new Vector2(), new Vector2(), new Vector2();
class Quaternion {
  constructor(t = 0, e = 0, i = 0, s = 1) {
    this.isQuaternion = true, this._x = t, this._y = e, this._z = i, this._w = s;
  }
  static slerpFlat(t, e, i, s, r, a, n) {
    let o = i[s + 0], h = i[s + 1], c = i[s + 2], l = i[s + 3];
    const u = r[a + 0], d = r[a + 1], m = r[a + 2], _ = r[a + 3];
    if (0 === n)
      return t[e + 0] = o, t[e + 1] = h, t[e + 2] = c, void (t[e + 3] = l);
    if (1 === n)
      return t[e + 0] = u, t[e + 1] = d, t[e + 2] = m, void (t[e + 3] = _);
    if (l !== _ || o !== u || h !== d || c !== m) {
      let t2 = 1 - n;
      const e2 = o * u + h * d + c * m + l * _, i2 = e2 >= 0 ? 1 : -1, s2 = 1 - e2 * e2;
      if (s2 > Number.EPSILON) {
        const r3 = Math.sqrt(s2), a2 = Math.atan2(r3, e2 * i2);
        t2 = Math.sin(t2 * a2) / r3, n = Math.sin(n * a2) / r3;
      }
      const r2 = n * i2;
      if (o = o * t2 + u * r2, h = h * t2 + d * r2, c = c * t2 + m * r2, l = l * t2 + _ * r2, t2 === 1 - n) {
        const t3 = 1 / Math.sqrt(o * o + h * h + c * c + l * l);
        o *= t3, h *= t3, c *= t3, l *= t3;
      }
    }
    t[e] = o, t[e + 1] = h, t[e + 2] = c, t[e + 3] = l;
  }
  static multiplyQuaternionsFlat(t, e, i, s, r, a) {
    const n = i[s], o = i[s + 1], h = i[s + 2], c = i[s + 3], l = r[a], u = r[a + 1], d = r[a + 2], m = r[a + 3];
    return t[e] = n * m + c * l + o * d - h * u, t[e + 1] = o * m + c * u + h * l - n * d, t[e + 2] = h * m + c * d + n * u - o * l, t[e + 3] = c * m - n * l - o * u - h * d, t;
  }
  get x() {
    return this._x;
  }
  set x(t) {
    this._x = t, this._onChangeCallback();
  }
  get y() {
    return this._y;
  }
  set y(t) {
    this._y = t, this._onChangeCallback();
  }
  get z() {
    return this._z;
  }
  set z(t) {
    this._z = t, this._onChangeCallback();
  }
  get w() {
    return this._w;
  }
  set w(t) {
    this._w = t, this._onChangeCallback();
  }
  set(t, e, i, s) {
    return this._x = t, this._y = e, this._z = i, this._w = s, this._onChangeCallback(), this;
  }
  clone() {
    return new this.constructor(this._x, this._y, this._z, this._w);
  }
  copy(t) {
    return this._x = t.x, this._y = t.y, this._z = t.z, this._w = t.w, this._onChangeCallback(), this;
  }
  setFromEuler(t, e = true) {
    const i = t._x, s = t._y, r = t._z, a = t._order, n = Math.cos, o = Math.sin, h = n(i / 2), c = n(s / 2), l = n(r / 2), u = o(i / 2), d = o(s / 2), m = o(r / 2);
    switch (a) {
      case "XYZ":
        this._x = u * c * l + h * d * m, this._y = h * d * l - u * c * m, this._z = h * c * m + u * d * l, this._w = h * c * l - u * d * m;
        break;
      case "YXZ":
        this._x = u * c * l + h * d * m, this._y = h * d * l - u * c * m, this._z = h * c * m - u * d * l, this._w = h * c * l + u * d * m;
        break;
      case "ZXY":
        this._x = u * c * l - h * d * m, this._y = h * d * l + u * c * m, this._z = h * c * m + u * d * l, this._w = h * c * l - u * d * m;
        break;
      case "ZYX":
        this._x = u * c * l - h * d * m, this._y = h * d * l + u * c * m, this._z = h * c * m - u * d * l, this._w = h * c * l + u * d * m;
        break;
      case "YZX":
        this._x = u * c * l + h * d * m, this._y = h * d * l + u * c * m, this._z = h * c * m - u * d * l, this._w = h * c * l - u * d * m;
        break;
      case "XZY":
        this._x = u * c * l - h * d * m, this._y = h * d * l - u * c * m, this._z = h * c * m + u * d * l, this._w = h * c * l + u * d * m;
        break;
      default:
        console.warn("THREE.Quaternion: .setFromEuler() encountered an unknown order: " + a);
    }
    return true === e && this._onChangeCallback(), this;
  }
  setFromAxisAngle(t, e) {
    const i = e / 2, s = Math.sin(i);
    return this._x = t.x * s, this._y = t.y * s, this._z = t.z * s, this._w = Math.cos(i), this._onChangeCallback(), this;
  }
  setFromRotationMatrix(t) {
    const e = t.elements, i = e[0], s = e[4], r = e[8], a = e[1], n = e[5], o = e[9], h = e[2], c = e[6], l = e[10], u = i + n + l;
    if (u > 0) {
      const t2 = 0.5 / Math.sqrt(u + 1);
      this._w = 0.25 / t2, this._x = (c - o) * t2, this._y = (r - h) * t2, this._z = (a - s) * t2;
    } else if (i > n && i > l) {
      const t2 = 2 * Math.sqrt(1 + i - n - l);
      this._w = (c - o) / t2, this._x = 0.25 * t2, this._y = (s + a) / t2, this._z = (r + h) / t2;
    } else if (n > l) {
      const t2 = 2 * Math.sqrt(1 + n - i - l);
      this._w = (r - h) / t2, this._x = (s + a) / t2, this._y = 0.25 * t2, this._z = (o + c) / t2;
    } else {
      const t2 = 2 * Math.sqrt(1 + l - i - n);
      this._w = (a - s) / t2, this._x = (r + h) / t2, this._y = (o + c) / t2, this._z = 0.25 * t2;
    }
    return this._onChangeCallback(), this;
  }
  setFromUnitVectors(t, e) {
    let i = t.dot(e) + 1;
    return i < 1e-8 ? (i = 0, Math.abs(t.x) > Math.abs(t.z) ? (this._x = -t.y, this._y = t.x, this._z = 0, this._w = i) : (this._x = 0, this._y = -t.z, this._z = t.y, this._w = i)) : (this._x = t.y * e.z - t.z * e.y, this._y = t.z * e.x - t.x * e.z, this._z = t.x * e.y - t.y * e.x, this._w = i), this.normalize();
  }
  angleTo(t) {
    return 2 * Math.acos(Math.abs(clamp(this.dot(t), -1, 1)));
  }
  rotateTowards(t, e) {
    const i = this.angleTo(t);
    if (0 === i)
      return this;
    const s = Math.min(1, e / i);
    return this.slerp(t, s), this;
  }
  identity() {
    return this.set(0, 0, 0, 1);
  }
  invert() {
    return this.conjugate();
  }
  conjugate() {
    return this._x *= -1, this._y *= -1, this._z *= -1, this._onChangeCallback(), this;
  }
  dot(t) {
    return this._x * t._x + this._y * t._y + this._z * t._z + this._w * t._w;
  }
  lengthSq() {
    return this._x * this._x + this._y * this._y + this._z * this._z + this._w * this._w;
  }
  length() {
    return Math.sqrt(this._x * this._x + this._y * this._y + this._z * this._z + this._w * this._w);
  }
  normalize() {
    let t = this.length();
    return 0 === t ? (this._x = 0, this._y = 0, this._z = 0, this._w = 1) : (t = 1 / t, this._x = this._x * t, this._y = this._y * t, this._z = this._z * t, this._w = this._w * t), this._onChangeCallback(), this;
  }
  multiply(t) {
    return this.multiplyQuaternions(this, t);
  }
  premultiply(t) {
    return this.multiplyQuaternions(t, this);
  }
  multiplyQuaternions(t, e) {
    const i = t._x, s = t._y, r = t._z, a = t._w, n = e._x, o = e._y, h = e._z, c = e._w;
    return this._x = i * c + a * n + s * h - r * o, this._y = s * c + a * o + r * n - i * h, this._z = r * c + a * h + i * o - s * n, this._w = a * c - i * n - s * o - r * h, this._onChangeCallback(), this;
  }
  slerp(t, e) {
    if (0 === e)
      return this;
    if (1 === e)
      return this.copy(t);
    const i = this._x, s = this._y, r = this._z, a = this._w;
    let n = a * t._w + i * t._x + s * t._y + r * t._z;
    if (n < 0 ? (this._w = -t._w, this._x = -t._x, this._y = -t._y, this._z = -t._z, n = -n) : this.copy(t), n >= 1)
      return this._w = a, this._x = i, this._y = s, this._z = r, this;
    const o = 1 - n * n;
    if (o <= Number.EPSILON) {
      const t2 = 1 - e;
      return this._w = t2 * a + e * this._w, this._x = t2 * i + e * this._x, this._y = t2 * s + e * this._y, this._z = t2 * r + e * this._z, this.normalize(), this;
    }
    const h = Math.sqrt(o), c = Math.atan2(h, n), l = Math.sin((1 - e) * c) / h, u = Math.sin(e * c) / h;
    return this._w = a * l + this._w * u, this._x = i * l + this._x * u, this._y = s * l + this._y * u, this._z = r * l + this._z * u, this._onChangeCallback(), this;
  }
  slerpQuaternions(t, e, i) {
    return this.copy(t).slerp(e, i);
  }
  random() {
    const t = 2 * Math.PI * Math.random(), e = 2 * Math.PI * Math.random(), i = Math.random(), s = Math.sqrt(1 - i), r = Math.sqrt(i);
    return this.set(s * Math.sin(t), s * Math.cos(t), r * Math.sin(e), r * Math.cos(e));
  }
  equals(t) {
    return t._x === this._x && t._y === this._y && t._z === this._z && t._w === this._w;
  }
  fromArray(t, e = 0) {
    return this._x = t[e], this._y = t[e + 1], this._z = t[e + 2], this._w = t[e + 3], this._onChangeCallback(), this;
  }
  toArray(t = [], e = 0) {
    return t[e] = this._x, t[e + 1] = this._y, t[e + 2] = this._z, t[e + 3] = this._w, t;
  }
  fromBufferAttribute(t, e) {
    return this._x = t.getX(e), this._y = t.getY(e), this._z = t.getZ(e), this._w = t.getW(e), this._onChangeCallback(), this;
  }
  toJSON() {
    return this.toArray();
  }
  _onChange(t) {
    return this._onChangeCallback = t, this;
  }
  _onChangeCallback() {
  }
  *[Symbol.iterator]() {
    yield this._x, yield this._y, yield this._z, yield this._w;
  }
}
class Vector3 {
  constructor(t = 0, e = 0, i = 0) {
    Vector3.prototype.isVector3 = true, this.x = t, this.y = e, this.z = i;
  }
  set(t, e, i) {
    return void 0 === i && (i = this.z), this.x = t, this.y = e, this.z = i, this;
  }
  setScalar(t) {
    return this.x = t, this.y = t, this.z = t, this;
  }
  setX(t) {
    return this.x = t, this;
  }
  setY(t) {
    return this.y = t, this;
  }
  setZ(t) {
    return this.z = t, this;
  }
  setComponent(t, e) {
    switch (t) {
      case 0:
        this.x = e;
        break;
      case 1:
        this.y = e;
        break;
      case 2:
        this.z = e;
        break;
      default:
        throw new Error("index is out of range: " + t);
    }
    return this;
  }
  getComponent(t) {
    switch (t) {
      case 0:
        return this.x;
      case 1:
        return this.y;
      case 2:
        return this.z;
      default:
        throw new Error("index is out of range: " + t);
    }
  }
  clone() {
    return new this.constructor(this.x, this.y, this.z);
  }
  copy(t) {
    return this.x = t.x, this.y = t.y, this.z = t.z, this;
  }
  add(t) {
    return this.x += t.x, this.y += t.y, this.z += t.z, this;
  }
  addScalar(t) {
    return this.x += t, this.y += t, this.z += t, this;
  }
  addVectors(t, e) {
    return this.x = t.x + e.x, this.y = t.y + e.y, this.z = t.z + e.z, this;
  }
  addScaledVector(t, e) {
    return this.x += t.x * e, this.y += t.y * e, this.z += t.z * e, this;
  }
  sub(t) {
    return this.x -= t.x, this.y -= t.y, this.z -= t.z, this;
  }
  subScalar(t) {
    return this.x -= t, this.y -= t, this.z -= t, this;
  }
  subVectors(t, e) {
    return this.x = t.x - e.x, this.y = t.y - e.y, this.z = t.z - e.z, this;
  }
  multiply(t) {
    return this.x *= t.x, this.y *= t.y, this.z *= t.z, this;
  }
  multiplyScalar(t) {
    return this.x *= t, this.y *= t, this.z *= t, this;
  }
  multiplyVectors(t, e) {
    return this.x = t.x * e.x, this.y = t.y * e.y, this.z = t.z * e.z, this;
  }
  applyEuler(t) {
    return this.applyQuaternion(_quaternion.setFromEuler(t));
  }
  applyAxisAngle(t, e) {
    return this.applyQuaternion(_quaternion.setFromAxisAngle(t, e));
  }
  applyMatrix3(t) {
    const e = this.x, i = this.y, s = this.z, r = t.elements;
    return this.x = r[0] * e + r[3] * i + r[6] * s, this.y = r[1] * e + r[4] * i + r[7] * s, this.z = r[2] * e + r[5] * i + r[8] * s, this;
  }
  applyNormalMatrix(t) {
    return this.applyMatrix3(t).normalize();
  }
  applyMatrix4(t) {
    const e = this.x, i = this.y, s = this.z, r = t.elements, a = 1 / (r[3] * e + r[7] * i + r[11] * s + r[15]);
    return this.x = (r[0] * e + r[4] * i + r[8] * s + r[12]) * a, this.y = (r[1] * e + r[5] * i + r[9] * s + r[13]) * a, this.z = (r[2] * e + r[6] * i + r[10] * s + r[14]) * a, this;
  }
  applyQuaternion(t) {
    const e = this.x, i = this.y, s = this.z, r = t.x, a = t.y, n = t.z, o = t.w, h = 2 * (a * s - n * i), c = 2 * (n * e - r * s), l = 2 * (r * i - a * e);
    return this.x = e + o * h + a * l - n * c, this.y = i + o * c + n * h - r * l, this.z = s + o * l + r * c - a * h, this;
  }
  project(t) {
    return this.applyMatrix4(t.matrixWorldInverse).applyMatrix4(t.projectionMatrix);
  }
  unproject(t) {
    return this.applyMatrix4(t.projectionMatrixInverse).applyMatrix4(t.matrixWorld);
  }
  transformDirection(t) {
    const e = this.x, i = this.y, s = this.z, r = t.elements;
    return this.x = r[0] * e + r[4] * i + r[8] * s, this.y = r[1] * e + r[5] * i + r[9] * s, this.z = r[2] * e + r[6] * i + r[10] * s, this.normalize();
  }
  divide(t) {
    return this.x /= t.x, this.y /= t.y, this.z /= t.z, this;
  }
  divideScalar(t) {
    return this.multiplyScalar(1 / t);
  }
  min(t) {
    return this.x = Math.min(this.x, t.x), this.y = Math.min(this.y, t.y), this.z = Math.min(this.z, t.z), this;
  }
  max(t) {
    return this.x = Math.max(this.x, t.x), this.y = Math.max(this.y, t.y), this.z = Math.max(this.z, t.z), this;
  }
  clamp(t, e) {
    return this.x = clamp(this.x, t.x, e.x), this.y = clamp(this.y, t.y, e.y), this.z = clamp(this.z, t.z, e.z), this;
  }
  clampScalar(t, e) {
    return this.x = clamp(this.x, t, e), this.y = clamp(this.y, t, e), this.z = clamp(this.z, t, e), this;
  }
  clampLength(t, e) {
    const i = this.length();
    return this.divideScalar(i || 1).multiplyScalar(clamp(i, t, e));
  }
  floor() {
    return this.x = Math.floor(this.x), this.y = Math.floor(this.y), this.z = Math.floor(this.z), this;
  }
  ceil() {
    return this.x = Math.ceil(this.x), this.y = Math.ceil(this.y), this.z = Math.ceil(this.z), this;
  }
  round() {
    return this.x = Math.round(this.x), this.y = Math.round(this.y), this.z = Math.round(this.z), this;
  }
  roundToZero() {
    return this.x = Math.trunc(this.x), this.y = Math.trunc(this.y), this.z = Math.trunc(this.z), this;
  }
  negate() {
    return this.x = -this.x, this.y = -this.y, this.z = -this.z, this;
  }
  dot(t) {
    return this.x * t.x + this.y * t.y + this.z * t.z;
  }
  lengthSq() {
    return this.x * this.x + this.y * this.y + this.z * this.z;
  }
  length() {
    return Math.sqrt(this.x * this.x + this.y * this.y + this.z * this.z);
  }
  manhattanLength() {
    return Math.abs(this.x) + Math.abs(this.y) + Math.abs(this.z);
  }
  normalize() {
    return this.divideScalar(this.length() || 1);
  }
  setLength(t) {
    return this.normalize().multiplyScalar(t);
  }
  lerp(t, e) {
    return this.x += (t.x - this.x) * e, this.y += (t.y - this.y) * e, this.z += (t.z - this.z) * e, this;
  }
  lerpVectors(t, e, i) {
    return this.x = t.x + (e.x - t.x) * i, this.y = t.y + (e.y - t.y) * i, this.z = t.z + (e.z - t.z) * i, this;
  }
  cross(t) {
    return this.crossVectors(this, t);
  }
  crossVectors(t, e) {
    const i = t.x, s = t.y, r = t.z, a = e.x, n = e.y, o = e.z;
    return this.x = s * o - r * n, this.y = r * a - i * o, this.z = i * n - s * a, this;
  }
  projectOnVector(t) {
    const e = t.lengthSq();
    if (0 === e)
      return this.set(0, 0, 0);
    const i = t.dot(this) / e;
    return this.copy(t).multiplyScalar(i);
  }
  projectOnPlane(t) {
    return _vector.copy(this).projectOnVector(t), this.sub(_vector);
  }
  reflect(t) {
    return this.sub(_vector.copy(t).multiplyScalar(2 * this.dot(t)));
  }
  angleTo(t) {
    const e = Math.sqrt(this.lengthSq() * t.lengthSq());
    if (0 === e)
      return Math.PI / 2;
    const i = this.dot(t) / e;
    return Math.acos(clamp(i, -1, 1));
  }
  distanceTo(t) {
    return Math.sqrt(this.distanceToSquared(t));
  }
  distanceToSquared(t) {
    const e = this.x - t.x, i = this.y - t.y, s = this.z - t.z;
    return e * e + i * i + s * s;
  }
  manhattanDistanceTo(t) {
    return Math.abs(this.x - t.x) + Math.abs(this.y - t.y) + Math.abs(this.z - t.z);
  }
  setFromSpherical(t) {
    return this.setFromSphericalCoords(t.radius, t.phi, t.theta);
  }
  setFromSphericalCoords(t, e, i) {
    const s = Math.sin(e) * t;
    return this.x = s * Math.sin(i), this.y = Math.cos(e) * t, this.z = s * Math.cos(i), this;
  }
  setFromCylindrical(t) {
    return this.setFromCylindricalCoords(t.radius, t.theta, t.y);
  }
  setFromCylindricalCoords(t, e, i) {
    return this.x = t * Math.sin(e), this.y = i, this.z = t * Math.cos(e), this;
  }
  setFromMatrixPosition(t) {
    const e = t.elements;
    return this.x = e[12], this.y = e[13], this.z = e[14], this;
  }
  setFromMatrixScale(t) {
    const e = this.setFromMatrixColumn(t, 0).length(), i = this.setFromMatrixColumn(t, 1).length(), s = this.setFromMatrixColumn(t, 2).length();
    return this.x = e, this.y = i, this.z = s, this;
  }
  setFromMatrixColumn(t, e) {
    return this.fromArray(t.elements, 4 * e);
  }
  setFromMatrix3Column(t, e) {
    return this.fromArray(t.elements, 3 * e);
  }
  setFromEuler(t) {
    return this.x = t._x, this.y = t._y, this.z = t._z, this;
  }
  setFromColor(t) {
    return this.x = t.r, this.y = t.g, this.z = t.b, this;
  }
  equals(t) {
    return t.x === this.x && t.y === this.y && t.z === this.z;
  }
  fromArray(t, e = 0) {
    return this.x = t[e], this.y = t[e + 1], this.z = t[e + 2], this;
  }
  toArray(t = [], e = 0) {
    return t[e] = this.x, t[e + 1] = this.y, t[e + 2] = this.z, t;
  }
  fromBufferAttribute(t, e) {
    return this.x = t.getX(e), this.y = t.getY(e), this.z = t.getZ(e), this;
  }
  random() {
    return this.x = Math.random(), this.y = Math.random(), this.z = Math.random(), this;
  }
  randomDirection() {
    const t = Math.random() * Math.PI * 2, e = 2 * Math.random() - 1, i = Math.sqrt(1 - e * e);
    return this.x = i * Math.cos(t), this.y = e, this.z = i * Math.sin(t), this;
  }
  *[Symbol.iterator]() {
    yield this.x, yield this.y, yield this.z;
  }
}
const _vector = new Vector3(), _quaternion = new Quaternion();
new Vector3(), new Vector3(), new Vector3(), new Vector3(), new Vector3(), new Vector3(), new Vector3(), new Vector3(), new Vector3(), new Vector3();
const _CoordTransformer = class {
  static _cacheKey(t, e) {
    return `${t}->${e}`;
  }
  static _clearPathCache() {
    _CoordTransformer._pathCache.clear();
  }
  static _findTransformPath(t, e, i = /* @__PURE__ */ new Set()) {
    const s = _CoordTransformer._cacheKey(t, e);
    if (_CoordTransformer._pathCache.has(s))
      return _CoordTransformer._pathCache.get(s);
    if (i.has(t))
      return _CoordTransformer._pathCache.set(s, null), null;
    if (i.add(t), _CoordTransformer._registeredTransformers[t] && _CoordTransformer._registeredTransformers[t][e]) {
      const i2 = [t, e];
      return _CoordTransformer._pathCache.set(s, i2), i2;
    }
    if (_CoordTransformer._registeredTransformers[t])
      for (const r of Object.keys(_CoordTransformer._registeredTransformers[t])) {
        const a = _CoordTransformer._findTransformPath(r, e, new Set(i));
        if (a) {
          const e2 = [t, ...a];
          return _CoordTransformer._pathCache.set(s, e2), e2;
        }
      }
    return _CoordTransformer._pathCache.set(s, null), null;
  }
  static transform(t, e, i, s) {
    if (t === e)
      return s.copy(i), s;
    const r = _CoordTransformer._findTransformPath(t, e);
    if (!r)
      return s.copy(i), s;
    let a = i;
    for (let t2 = 0; t2 < r.length - 1; t2++) {
      const e2 = r[t2], i2 = r[t2 + 1];
      a = _CoordTransformer._registeredTransformers[e2][i2](a, s);
    }
    return a;
  }
  static register(t, e, i) {
    _CoordTransformer._registeredTransformers[t] || (_CoordTransformer._registeredTransformers[t] = {}), _CoordTransformer._registeredTransformers[t][e] = i, _CoordTransformer._clearPathCache();
  }
  static unregister(t, e) {
    _CoordTransformer._registeredTransformers[t] && (delete _CoordTransformer._registeredTransformers[t][e], _CoordTransformer._clearPathCache());
  }
  static canTransform(t, e) {
    if (t === e)
      return false;
    return null !== _CoordTransformer._findTransformPath(t, e);
  }
  static serialize() {
    const t = {};
    for (const e of Object.keys(_CoordTransformer._registeredTransformers)) {
      t[e] = {};
      for (const i of Object.keys(_CoordTransformer._registeredTransformers[e]))
        t[e][i] = _CoordTransformer._registeredTransformers[e][i].toString();
    }
    return JSON.stringify(t);
  }
  static deserialize(json) {
    const result = JSON.parse(json);
    for (const srcCoord of Object.keys(result))
      for (const targetCoord of Object.keys(result[srcCoord]))
        _CoordTransformer.register(srcCoord, targetCoord, eval(result[srcCoord][targetCoord]));
  }
};
let CoordTransformer = _CoordTransformer;
__publicField(CoordTransformer, "_registeredTransformers", {});
__publicField(CoordTransformer, "_pathCache", /* @__PURE__ */ new Map());
const _pointIn = new Vector3$1(), _pointOut = new Vector3$1(), reprojectCoordinate = (t, e, i, s, r, a) => {
  let n = CoordTransformer.canTransform(t, e);
  return i !== s && i && s || n ? (i.unprojectCoordinate(r, _pointOut), n ? CoordTransformer.transform(t, e, _pointOut, _pointIn) : _pointIn.copy(_pointOut), s.projectCoordinate(_pointIn, a), a) : (a.copy(r), a);
}, projectVertices = (t, e, i = true, s = true, r = false) => {
  if (!e.forceProjectCoordinates && !r && e.targetProjectionName === e.sourceProjectionName)
    return;
  const a = e.sourceProjection, n = e.targetProjection, o = e.sourceCoordType, h = e.targetCoordType, [c, l, u] = e.targetCenter, d = e.forceUseGeoBoundingBox || a.isGeo, m = d ? e.geoBoundingBox : e.projectedBoundingBox;
  let _, f, p, x;
  if (m.isBox3) {
    const t2 = m.min, e2 = m.max;
    _ = t2.x, f = t2.y, p = e2.x, x = e2.y;
  } else
    [_, f, , p, x] = m;
  const y = p - _, M = x - f;
  if (i)
    for (let e2 = 0, i2 = t.length - 2; e2 < i2; e2 += 3) {
      const i3 = t[e2], r2 = t[e2 + 1], m2 = t[e2 + 2];
      _pointIn.set(_ + (i3 + 0.5) * y, f + (r2 + 0.5) * M, m2), d ? (o !== h && CoordTransformer.transform(o, h, _pointIn, _pointIn), n.projectCoordinate(_pointIn, _pointOut)) : reprojectCoordinate(o, h, a, n, _pointIn, _pointOut), t[e2] = _pointOut.x, t[e2 + 1] = _pointOut.y, t[e2 + 2] = _pointOut.z, s && (t[e2] -= c, t[e2 + 1] -= l, t[e2 + 2] -= u);
    }
  else
    for (let e2 = 0, i2 = t.length; e2 < i2; e2 += 1) {
      const i3 = t[e2], r2 = i3[0], m2 = i3[1], p2 = i3[2];
      _pointIn.set(_ + (r2 + 0.5) * y, f + (m2 + 0.5) * M, p2), d ? (o !== h && CoordTransformer.transform(o, h, _pointIn, _pointIn), n.projectCoordinate(_pointIn, _pointOut)) : reprojectCoordinate(o, h, a, n, _pointIn, _pointOut), i3[0] = _pointOut.x, i3[1] = _pointOut.y, i3[2] = _pointOut.z, s && (i3[0] -= c, i3[1] -= l, i3[2] -= u);
    }
}, PROJECTION_GEO = "EPSG:4326", PROJECTION_WEB_MERCATOR = "EPSG:3857", PROJECTION_ECEF = "EPSG:4978", PROJECTION_BD_MERCATOR = "BD:MERCATOR", PROJECTION_SCREEN_PIXEL = "SCREEN_PIXEL", defaultGeoBoundingBox = new Box3(new Vector3$1(-180, -90, -100), new Vector3$1(180, 90, 100));
defaultGeoBoundingBox.isDefault = true, Object.freeze(defaultGeoBoundingBox), Object.freeze(defaultGeoBoundingBox.min), Object.freeze(defaultGeoBoundingBox.max);
const _tempVector3In = new Vector3$1(), _tempVector3Out = new Vector3$1(), projectBoundingBoxMethods = { MIN_MAX: 1, FOUR_CORNERS: 2, FOUR_CORNERS_WITH_EQUATOR: 3 };
class Projection {
  constructor() {
    __publicField(this, "isProjection", true);
    __publicField(this, "isGeo", false);
    __publicField(this, "isAxisAligned", false);
    __publicField(this, "projectBoundingBoxMethod", projectBoundingBoxMethods.MIN_MAX);
  }
  projectCoordinate(t, e) {
    throw new Error("projectCoordinate() must be implemented in derived classes");
  }
  unprojectCoordinate(t, e) {
    throw new Error("unprojectCoordinate() must be implemented in derived classes");
  }
  geoBoxToProjectedBox(t, e, i = true) {
    if (e || (e = new Box3()), this.projectBoundingBoxMethod === projectBoundingBoxMethods.MIN_MAX)
      this.projectCoordinate(t.min, e.min, i), this.projectCoordinate(t.max, e.max, i);
    else if (this.projectBoundingBoxMethod === projectBoundingBoxMethods.FOUR_CORNERS || this.projectBoundingBoxMethod === projectBoundingBoxMethods.FOUR_CORNERS_WITH_EQUATOR) {
      let { x: s, y: r, z: a } = t.min, { x: n, y: o, z: h } = t.max;
      _tempVector3In.set(s, r, a), this.projectCoordinate(_tempVector3In, _tempVector3Out, i), e.expandByPoint(_tempVector3Out), _tempVector3In.set(n, o, h), this.projectCoordinate(_tempVector3In, _tempVector3Out, i), e.expandByPoint(_tempVector3Out), _tempVector3In.set(s, o, 0), this.projectCoordinate(_tempVector3In, _tempVector3Out, i), e.expandByPoint(_tempVector3Out), _tempVector3In.set(n, r, 0), this.projectCoordinate(_tempVector3In, _tempVector3Out, i), e.expandByPoint(_tempVector3Out), this.projectBoundingBoxMethod === projectBoundingBoxMethods.FOUR_CORNERS_WITH_EQUATOR && r < 0 && o > 0 && (_tempVector3In.set(s, 0, 0), this.projectCoordinate(_tempVector3In, _tempVector3Out, i), e.expandByPoint(_tempVector3Out), _tempVector3In.set(n, 0, 0), this.projectCoordinate(_tempVector3In, _tempVector3Out, i), e.expandByPoint(_tempVector3Out));
    }
    return e;
  }
  getGeodeticSurfaceNormal(t, e) {
    return e || (e = new Vector3$1()), e.set(0, 0, 1), e;
  }
  getProjectedSurfaceNormal(t, e) {
    return e || (e = new Vector3$1()), e.set(0, 0, 1), e;
  }
  projectedBoxToGeoBox(t, e, i = true) {
    return e || (e = new Box3()), this.unprojectCoordinate(t.min, e.min, i), this.unprojectCoordinate(t.max, e.max, i), e;
  }
  equals(t) {
    return !!t && this.name === t.name;
  }
  localFrameToFixedFrame(t, e) {
    return e || (e = new Matrix4()), e.identity(), e.setPosition(t), e;
  }
  get geoBoundingBox() {
    return this._geoBoundingBox || defaultGeoBoundingBox;
  }
  get projectedBoundingBox() {
    if (!this._projectedBoundingBox) {
      const t = this.geoBoundingBox;
      this._projectedBoundingBox = this.geoBoxToProjectedBox(t, null, false);
    }
    return this._projectedBoundingBox;
  }
}
const extendUnprojectCoordinate = (t, e, i, s, r = false) => {
  if (Math.abs(t) < s)
    return e;
  const a = t > 0 ? 1 : -1;
  if (r)
    return a * i;
  return a * (i * (1 + (Math.abs(t) - s) / s));
}, extendProjectCoordinate = (t, e, i, s, r = false) => {
  if (Math.abs(t) < i)
    return e;
  const a = t > 0 ? 1 : -1;
  if (r)
    return a * s;
  return a * (s * (1 + (Math.abs(t) - i) / i));
}, D2R = Math.PI / 180, A = 6378137, MAXEXTENT = 20037508, R2D = 180 / Math.PI, MAXLON = 85.0511287798;
function toMercator(t, e = null, i = false) {
  var s = Math.abs(t[0]) <= 180 ? t[0] : t[0] - 360 * sign(t[0]);
  const r = e || [0, 0];
  return r[0] = A * s * D2R, r[1] = A * Math.log(Math.tan(0.25 * Math.PI + 0.5 * t[1] * D2R)), i ? (r[0] = extendProjectCoordinate(t[0], r[0], 180, MAXEXTENT), r[1] = extendProjectCoordinate(t[1], r[1], MAXLON, MAXEXTENT)) : (r[0] > MAXEXTENT && (r[0] = MAXEXTENT), r[0] < -MAXEXTENT && (r[0] = -MAXEXTENT), r[1] > MAXEXTENT && (r[1] = MAXEXTENT), r[1] < -MAXEXTENT && (r[1] = -MAXEXTENT)), r;
}
function toWgs84(t, e, i = false) {
  const s = e || [0, 0];
  return s[0] = t[0] * R2D / A, s[1] = (0.5 * Math.PI - 2 * Math.atan(Math.exp(-t[1] / A))) * R2D, i && (s[0] = extendUnprojectCoordinate(t[0], s[0], 180, MAXEXTENT), s[1] = extendUnprojectCoordinate(t[1], s[1], MAXLON, MAXEXTENT)), s;
}
function sign(t) {
  return t < 0 ? -1 : t > 0 ? 1 : 0;
}
const _tempCoordinate = [0, 0], _inputArray$1 = [0, 0];
class WebMercatorProjection extends Projection {
  constructor() {
    super(...arguments);
    __publicField(this, "name", PROJECTION_WEB_MERCATOR);
    __publicField(this, "isAxisAligned", true);
  }
  projectCoordinate(t, e, i = false) {
    _inputArray$1[0] = t.x, _inputArray$1[1] = t.y, i || (_inputArray$1[0] < -180 && (_inputArray$1[0] = -180), _inputArray$1[0] > 180 && (_inputArray$1[0] = 180), _inputArray$1[1] < -85.0511287798 && (_inputArray$1[1] = -85.0511287798), _inputArray$1[1] > 85.0511287798 && (_inputArray$1[1] = 85.0511287798));
    const s = toMercator(_inputArray$1, _tempCoordinate, i);
    return e || (e = new Vector3$1()), e.x = s[0], e.y = s[1], e.z = t.z, e;
  }
  unprojectCoordinate(t, e, i = false) {
    const s = toWgs84([t.x, t.y], _tempCoordinate, i);
    return e || (e = new Vector3$1()), e.x = s[0], e.y = s[1], e.z = t.z, e;
  }
}
const Intersect = { OUTSIDE: -1, INTERSECTING: 0, INSIDE: 1 };
var Intersect$1 = Object.freeze(Intersect);
class OrientedBoundingBox {
  constructor(t, e) {
    this.isOrientedBoundingBox = true, this.center = Cartesian3.clone(defaultValue(t, Cartesian3.ZERO), new Vector3$1()), this.halfAxes = StaticMatrix3.clone(defaultValue(e, StaticMatrix3.ZERO));
  }
  intersectPlane(t) {
    return OrientedBoundingBox.intersectPlane(this, t);
  }
  distanceSquaredTo(t) {
    return OrientedBoundingBox.distanceSquaredTo(this, t);
  }
  computeCorners(t) {
    return OrientedBoundingBox.computeCorners(this, t);
  }
  getCenter(t) {
    return defined$1(t) ? (t.copy(this.center), t) : this.center.clone();
  }
  intersectsObb(t) {
    const e = this.center, i = t.center, s = this.halfAxes, r = t.halfAxes, a = new Vector3$1().subVectors(i, e), n = new Vector3$1(s.elements[0], s.elements[1], s.elements[2]), o = new Vector3$1(s.elements[3], s.elements[4], s.elements[5]), h = new Vector3$1(s.elements[6], s.elements[7], s.elements[8]), c = new Vector3$1(r.elements[0], r.elements[1], r.elements[2]), l = new Vector3$1(r.elements[3], r.elements[4], r.elements[5]), u = new Vector3$1(r.elements[6], r.elements[7], r.elements[8]), d = n.length(), m = o.length(), _ = h.length();
    n.normalize(), o.normalize(), h.normalize();
    const f = c.length(), p = l.length(), x = u.length();
    let y, M, g;
    return c.normalize(), l.normalize(), u.normalize(), y = d, M = f * Math.abs(n.dot(c)) + p * Math.abs(n.dot(l)) + x * Math.abs(n.dot(u)), g = Math.abs(a.dot(n)), !(g > y + M) && (y = m, M = f * Math.abs(o.dot(c)) + p * Math.abs(o.dot(l)) + x * Math.abs(o.dot(u)), g = Math.abs(a.dot(o)), !(g > y + M) && (y = _, M = f * Math.abs(h.dot(c)) + p * Math.abs(h.dot(l)) + x * Math.abs(h.dot(u)), g = Math.abs(a.dot(h)), !(g > y + M) && (y = d * Math.abs(c.dot(n)) + m * Math.abs(c.dot(o)) + _ * Math.abs(c.dot(h)), M = f, g = Math.abs(a.dot(c)), !(g > y + M) && (y = d * Math.abs(l.dot(n)) + m * Math.abs(l.dot(o)) + _ * Math.abs(l.dot(h)), M = p, g = Math.abs(a.dot(l)), !(g > y + M) && (y = d * Math.abs(u.dot(n)) + m * Math.abs(u.dot(o)) + _ * Math.abs(u.dot(h)), M = x, g = Math.abs(a.dot(u)), !(g > y + M))))));
  }
}
const scratchOffset = new Vector3$1(), scratchScale = new Vector3$1();
function fromPlaneExtents(t, e, i, s, r, a, n, o, h, c, l) {
  if (!(defined$1(r) && defined$1(a) && defined$1(n) && defined$1(o) && defined$1(h) && defined$1(c)))
    throw new DeveloperError("all extents (minimum/maximum X/Y/Z) are required.");
  defined$1(l) || (l = new OrientedBoundingBox());
  const u = l.halfAxes;
  StaticMatrix3.setColumn(u, 0, e, u), StaticMatrix3.setColumn(u, 1, i, u), StaticMatrix3.setColumn(u, 2, s, u);
  let d = scratchOffset;
  d.x = (r + a) / 2, d.y = (n + o) / 2, d.z = (h + c) / 2;
  const m = scratchScale;
  m.x = (a - r) / 2, m.y = (o - n) / 2, m.z = (c - h) / 2;
  const _ = l.center;
  return d = StaticMatrix3.multiplyByVector(u, d, d), Cartesian3.add(t, d, _), StaticMatrix3.multiplyByScale(u, m, u), l;
}
const scratchRectangleCenterCartographic = new Vector3$1(), scratchRectangleCenter = new Vector3$1(), scratchPerimeterCartographicNC = new Vector3$1(), scratchPerimeterCartographicNW = new Vector3$1(), scratchPerimeterCartographicCW = new Vector3$1(), scratchPerimeterCartographicSW = new Vector3$1(), scratchPerimeterCartographicSC = new Vector3$1(), scratchPerimeterCartesianNC = new Vector3$1(), scratchPerimeterCartesianNW = new Vector3$1(), scratchPerimeterCartesianCW = new Vector3$1(), scratchPerimeterCartesianSW = new Vector3$1(), scratchPerimeterCartesianSC = new Vector3$1(), scratchPerimeterProjectedNC = new Vector2(), scratchPerimeterProjectedNW = new Vector2(), scratchPerimeterProjectedCW = new Vector2(), scratchPerimeterProjectedSW = new Vector2(), scratchPerimeterProjectedSC = new Vector2(), scratchPlaneOrigin = new Vector3$1(), scratchPlaneNormal = new Vector3$1(), scratchPlaneXAxis = new Vector3$1(), scratchHorizonCartesian = new Vector3$1(), scratchHorizonProjected = new Vector2(), scratchMaxY = new Vector3$1(), scratchMinY = new Vector3$1(), scratchZ = new Vector3$1(), scratchPlane = new Plane(new Vector3$1(1, 0, 0), 0);
OrientedBoundingBox.fromRectangle = function(t, e, i, s, r) {
  if (!defined$1(t))
    throw new DeveloperError("rectangle is required");
  if (t.width < 0 || t.width > CesiumMath.TWO_PI)
    throw new DeveloperError("Rectangle width must be between 0 and 2 * pi");
  if (t.height < 0 || t.height > CesiumMath.PI)
    throw new DeveloperError("Rectangle height must be between 0 and pi");
  if (defined$1(s) && !CesiumMath.equalsEpsilon(s.radii.x, s.radii.y, CesiumMath.EPSILON15))
    throw new DeveloperError("Ellipsoid must be an ellipsoid of revolution (radii.x == radii.y)");
  let a, n, o, h, c, l, u;
  if (e = defaultValue(e, 0), i = defaultValue(i, 0), s = defaultValue(s, Ellipsoid.WGS84), t.width <= CesiumMath.PI) {
    const d2 = Rectangle.center(t, scratchRectangleCenterCartographic), m2 = s.cartographicToCartesian(d2, scratchRectangleCenter), _2 = new EllipsoidTangentPlane(m2, s);
    u = _2.plane;
    const f2 = d2.x, p2 = t.south < 0 && t.north > 0 ? 0 : d2.y, x2 = Cartographic.fromRadians(f2, t.north, i, scratchPerimeterCartographicNC), y2 = Cartographic.fromRadians(t.west, t.north, i, scratchPerimeterCartographicNW), M2 = Cartographic.fromRadians(t.west, p2, i, scratchPerimeterCartographicCW), g2 = Cartographic.fromRadians(t.west, t.south, i, scratchPerimeterCartographicSW), w2 = Cartographic.fromRadians(f2, t.south, i, scratchPerimeterCartographicSC), P = s.cartographicToCartesian(x2, scratchPerimeterCartesianNC);
    let S = s.cartographicToCartesian(y2, scratchPerimeterCartesianNW);
    const C = s.cartographicToCartesian(M2, scratchPerimeterCartesianCW);
    let E = s.cartographicToCartesian(g2, scratchPerimeterCartesianSW);
    const v = s.cartographicToCartesian(w2, scratchPerimeterCartesianSC), b = _2.projectPointToNearestOnPlane(P, scratchPerimeterProjectedNC), z = _2.projectPointToNearestOnPlane(S, scratchPerimeterProjectedNW), T = _2.projectPointToNearestOnPlane(C, scratchPerimeterProjectedCW), A2 = _2.projectPointToNearestOnPlane(E, scratchPerimeterProjectedSW), O = _2.projectPointToNearestOnPlane(v, scratchPerimeterProjectedSC);
    return a = Math.min(z.x, T.x, A2.x), n = -a, h = Math.max(z.y, b.y), o = Math.min(A2.y, O.y), y2.z = g2.z = e, S = s.cartographicToCartesian(y2, scratchPerimeterCartesianNW), E = s.cartographicToCartesian(g2, scratchPerimeterCartesianSW), c = Math.min(StaticPlane.getPointDistance(u, S), StaticPlane.getPointDistance(u, E)), l = i, fromPlaneExtents(_2.origin, _2.xAxis, _2.yAxis, _2.zAxis, a, n, o, h, c, l, r);
  }
  const d = t.south > 0, m = t.north < 0, _ = d ? t.south : m ? t.north : 0, f = Rectangle.center(t, scratchRectangleCenterCartographic).x, p = Cartesian3.fromRadians(f, _, i, s, scratchPlaneOrigin);
  p.z = 0;
  const x = Math.abs(p.x) < CesiumMath.EPSILON10 && Math.abs(p.y) < CesiumMath.EPSILON10 ? Cartesian3.UNIT_X : Cartesian3.normalize(p, scratchPlaneNormal), y = Cartesian3.UNIT_Z, M = Cartesian3.cross(x, y, scratchPlaneXAxis);
  u = StaticPlane.fromPointNormal(p, x, scratchPlane);
  const g = Cartesian3.fromRadians(f + CesiumMath.PI_OVER_TWO, _, i, s, scratchHorizonCartesian);
  n = Cartesian3.dot(StaticPlane.projectPointOntoPlane(u, g, scratchHorizonProjected), M), a = -n, h = Cartesian3.fromRadians(0, t.north, m ? e : i, s, scratchMaxY).z, o = Cartesian3.fromRadians(0, t.south, d ? e : i, s, scratchMinY).z;
  const w = Cartesian3.fromRadians(t.east, _, i, s, scratchZ);
  return c = StaticPlane.getPointDistance(u, w), l = 0, fromPlaneExtents(p, M, y, x, a, n, o, h, c, l, r);
};
const scratchCartesianU = new Vector3$1(), scratchCartesianV = new Vector3$1(), scratchCartesianW = new Vector3$1(), scratchValidAxis2 = new Vector3$1(), scratchValidAxis3 = new Vector3$1(), scratchPPrime = new Vector3$1();
OrientedBoundingBox.distanceSquaredTo = function(t, e) {
  if (!defined$1(t))
    throw new DeveloperError("box is required.");
  if (!defined$1(e))
    throw new DeveloperError("cartesian is required.");
  const i = Cartesian3.subtract(e, t.center, scratchOffset), s = t.halfAxes;
  let r = StaticMatrix3.getColumn(s, 0, scratchCartesianU), a = StaticMatrix3.getColumn(s, 1, scratchCartesianV), n = StaticMatrix3.getColumn(s, 2, scratchCartesianW);
  const o = Cartesian3.magnitude(r), h = Cartesian3.magnitude(a), c = Cartesian3.magnitude(n);
  let l = true, u = true, d = true;
  o > 0 ? Cartesian3.divideByScalar(r, o, r) : l = false, h > 0 ? Cartesian3.divideByScalar(a, h, a) : u = false, c > 0 ? Cartesian3.divideByScalar(n, c, n) : d = false;
  const m = !l + !u + !d;
  let _, f, p;
  if (1 === m) {
    let t2 = r;
    _ = a, f = n, u ? d || (t2 = n, f = r) : (t2 = a, _ = r), p = Cartesian3.cross(_, f, scratchValidAxis3), t2 === r ? r = p : t2 === a ? a = p : t2 === n && (n = p);
  } else if (2 === m) {
    _ = r, u ? _ = a : d && (_ = n);
    let t2 = Cartesian3.UNIT_Y;
    Cartesian3.equalsEpsilon(t2, _, CesiumMath.EPSILON3) && (t2 = Cartesian3.UNIT_X), f = Cartesian3.cross(_, t2, scratchValidAxis2), Cartesian3.normalize(f, f), p = Cartesian3.cross(_, f, scratchValidAxis3), Cartesian3.normalize(p, p), _ === r ? (a = f, n = p) : _ === a ? (n = f, r = p) : _ === n && (r = f, a = p);
  } else
    3 === m && (r = Cartesian3.UNIT_X, a = Cartesian3.UNIT_Y, n = Cartesian3.UNIT_Z);
  const x = scratchPPrime;
  x.x = Cartesian3.dot(i, r), x.y = Cartesian3.dot(i, a), x.z = Cartesian3.dot(i, n);
  let y, M = 0;
  return x.x < -o ? (y = x.x + o, M += y * y) : x.x > o && (y = x.x - o, M += y * y), x.y < -h ? (y = x.y + h, M += y * y) : x.y > h && (y = x.y - h, M += y * y), x.z < -c ? (y = x.z + c, M += y * y) : x.z > c && (y = x.z - c, M += y * y), M;
}, OrientedBoundingBox.intersectPlane = function(t, e) {
  if (!defined$1(t))
    throw new DeveloperError("box is required.");
  if (!defined$1(e))
    throw new DeveloperError("plane is required.");
  const i = t.center, s = e.normal, r = t.halfAxes, a = s.x, n = s.y, o = s.z, h = r.elements, c = Math.abs(a * h[StaticMatrix3.COLUMN0ROW0] + n * h[StaticMatrix3.COLUMN0ROW1] + o * h[StaticMatrix3.COLUMN0ROW2]) + Math.abs(a * h[StaticMatrix3.COLUMN1ROW0] + n * h[StaticMatrix3.COLUMN1ROW1] + o * h[StaticMatrix3.COLUMN1ROW2]) + Math.abs(a * h[StaticMatrix3.COLUMN2ROW0] + n * h[StaticMatrix3.COLUMN2ROW1] + o * h[StaticMatrix3.COLUMN2ROW2]), l = Cartesian3.dot(s.clone(), i) + e.constant;
  return l <= -c ? Intersect$1.OUTSIDE : l >= c ? Intersect$1.INSIDE : Intersect$1.INTERSECTING;
};
const scratchXAxis = new Vector3$1(), scratchYAxis = new Vector3$1(), scratchZAxis = new Vector3$1();
OrientedBoundingBox.computeCorners = function(t, e) {
  defined$1(e) || (e = [new Vector3$1(), new Vector3$1(), new Vector3$1(), new Vector3$1(), new Vector3$1(), new Vector3$1(), new Vector3$1(), new Vector3$1()]);
  const i = t.center, s = t.halfAxes, r = StaticMatrix3.getColumn(s, 0, scratchXAxis), a = StaticMatrix3.getColumn(s, 1, scratchYAxis), n = StaticMatrix3.getColumn(s, 2, scratchZAxis);
  return Cartesian3.clone(i, e[0]), Cartesian3.subtract(e[0], r, e[0]), Cartesian3.subtract(e[0], a, e[0]), Cartesian3.subtract(e[0], n, e[0]), Cartesian3.clone(i, e[1]), Cartesian3.subtract(e[1], r, e[1]), Cartesian3.subtract(e[1], a, e[1]), Cartesian3.add(e[1], n, e[1]), Cartesian3.clone(i, e[2]), Cartesian3.subtract(e[2], r, e[2]), Cartesian3.add(e[2], a, e[2]), Cartesian3.subtract(e[2], n, e[2]), Cartesian3.clone(i, e[3]), Cartesian3.subtract(e[3], r, e[3]), Cartesian3.add(e[3], a, e[3]), Cartesian3.add(e[3], n, e[3]), Cartesian3.clone(i, e[4]), Cartesian3.add(e[4], r, e[4]), Cartesian3.subtract(e[4], a, e[4]), Cartesian3.subtract(e[4], n, e[4]), Cartesian3.clone(i, e[5]), Cartesian3.add(e[5], r, e[5]), Cartesian3.subtract(e[5], a, e[5]), Cartesian3.add(e[5], n, e[5]), Cartesian3.clone(i, e[6]), Cartesian3.add(e[6], r, e[6]), Cartesian3.add(e[6], a, e[6]), Cartesian3.subtract(e[6], n, e[6]), Cartesian3.clone(i, e[7]), Cartesian3.add(e[7], r, e[7]), Cartesian3.add(e[7], a, e[7]), Cartesian3.add(e[7], n, e[7]), e;
}, OrientedBoundingBox.fromGeoBoundingBox = function(t, e) {
  if (!defined$1(t))
    throw new DeveloperError("geoBoundingBox is required.");
  const i = t.min, s = t.max, r = Rectangle.fromBox(t, null, true);
  return OrientedBoundingBox.fromRectangle(r, i.z, s.z, null, e);
};
class ECEFProjection extends Projection {
  constructor() {
    super(...arguments);
    __publicField(this, "name", PROJECTION_ECEF);
  }
  projectCoordinate(t, e) {
    return Ellipsoid.WGS84.cartographicDegreeToCartesian(t, e);
  }
  unprojectCoordinate(t, e) {
    return Ellipsoid.WGS84.cartesianToCartographicDegree(t, e);
  }
  getGeodeticSurfaceNormal(t, e) {
    e || (e = new Vector3$1());
    return Ellipsoid.WGS84.geodeticSurfaceNormalCartographic(t, e);
  }
  getProjectedSurfaceNormal(t, e) {
    e || (e = new Vector3$1());
    return Ellipsoid.WGS84.geodeticSurfaceNormal(t, e);
  }
  geoBoxToProjectedBox(t, e) {
    return e || (e = new OrientedBoundingBox()), e = OrientedBoundingBox.fromGeoBoundingBox(t, e);
  }
  getLODSacleOfGeoBoundingBox(t) {
    if (t.min.y > 85 || t.max.y < -85)
      return 0;
    const e = (t.min.y + t.max.y) / 2;
    return Math.cos(MathUtils.degToRad(e));
  }
  localFrameToFixedFrame(t, e) {
    return e || (e = new Matrix4()), Transforms.eastNorthUpToFixedFrame(t, null, e), e;
  }
}
function MercatorProjection() {
}
function extend(t, e) {
  for (let i in e)
    t[i] = e[i];
}
function Point(t, e) {
  this.lng = t, this.lat = e;
}
function Pixel(t, e) {
  this.x = t, this.y = e;
}
extend(Point.prototype, { equals: function(t) {
  return this.lat === t.lat && this.lng === t.lng;
}, clone: function() {
  return new Point(this.lat, this.lng);
}, getLngSpan: function(t) {
  let e = this.lng, i = Math.abs(t - e);
  return i > 180 && (i = 360 - i), i;
}, sub: function(t) {
  return new Point(this.lat - t.lat, this.lng - t.lng);
}, toString: function() {
  return "Point";
} }), extend(MercatorProjection, { EARTHRADIUS: 637099681e-2, MCBAND: [1289059486e-2, 836237787e-2, 5591021, 348198983e-2, 167804312e-2, 0], LLBAND: [75, 60, 45, 30, 15, 0], MC2LL: [[1410526172116255e-23, 898305509648872e-20, -1.9939833816331, 200.9824383106796, -187.2403703815547, 91.6087516669843, -23.38765649603339, 2.57121317296198, -0.03801003308653, 173379812e-1], [-7435856389565537e-24, 8983055097726239e-21, -0.78625201886289, 96.32687599759846, -1.85204757529826, -59.36935905485877, 47.40033549296737, -16.50741931063887, 2.28786674699375, 1026014486e-2], [-3030883460898826e-23, 898305509983578e-20, 0.30071316287616, 59.74293618442277, 7.357984074871, -25.38371002664745, 13.45380521110908, -3.29883767235584, 0.32710905363475, 685681737e-2], [-1981981304930552e-23, 8983055099779535e-21, 0.03278182852591, 40.31678527705744, 0.65659298677277, -4.44255534477492, 0.85341911805263, 0.12923347998204, -0.04625736007561, 448277706e-2], [309191371068437e-23, 8983055096812155e-21, 6995724062e-14, 23.10934304144901, -23663490511e-14, -0.6321817810242, -0.00663494467273, 0.03430082397953, -0.00466043876332, 25551644e-1], [2890871144776878e-24, 8983055095805407e-21, -3068298e-14, 7.47137025468032, -353937994e-14, -0.02145144861037, -1234426596e-14, 10322952773e-14, -323890364e-14, 826088.5]], LL2MC: [[-0.0015702102444, 111320.7020616939, 1704480524535203, -10338987376042340, 26112667856603880, -35149669176653700, 26595700718403920, -10725012454188240, 1800819912950474, 82.5], [8277824516172526e-19, 111320.7020463578, 6477955746671607e-7, -4082003173641316e-6, 1077490566351142e-5, -1517187553151559e-5, 1205306533862167e-5, -5124939663577472e-6, 9133119359512032e-7, 67.5], [0.00337398766765, 111320.7020202162, 4481351045890365e-9, -2339375119931662e-8, 7968221547186455e-8, -1159649932797253e-7, 9723671115602145e-8, -4366194633752821e-8, 8477230501135234e-9, 52.5], [0.00220636496208, 111320.7020209128, 51751.86112841131, 3796837749470245e-9, 992013.7397791013, -122195221711287e-8, 1340652697009075e-9, -620943.6990984312, 144416.9293806241, 37.5], [-3441963504368392e-19, 111320.7020576856, 278.2353980772752, 2485758690035394e-9, 6070.750963243378, 54821.18345352118, 9540.606633304236, -2710.55326746645, 1405.483844121726, 22.5], [-3218135878613132e-19, 111320.7020701615, 0.00369383431289, 823725.6402795718, 0.46104986909093, 2351.343141331292, 1.58060784298199, 8.77738589078284, 0.37238884252424, 7.45]], getDistanceByMC: function(t, e) {
  if (!t || !e)
    return 0;
  let i, s, r, a;
  return (t = this.convertMC2LL(t)) ? (i = this.toRadians(t.lng), s = this.toRadians(t.lat), (e = this.convertMC2LL(e)) ? (r = this.toRadians(e.lng), a = this.toRadians(e.lat), this.getDistance(i, r, s, a)) : 0) : 0;
}, getDistanceByLL: function(t, e) {
  if (!t || !e)
    return 0;
  let i, s, r, a;
  return t.lng = this.getLoop(t.lng, -180, 180), t.lat = this.getRange(t.lat, -74, 74), e.lng = this.getLoop(e.lng, -180, 180), e.lat = this.getRange(e.lat, -74, 74), i = this.toRadians(t.lng), r = this.toRadians(t.lat), s = this.toRadians(e.lng), a = this.toRadians(e.lat), this.getDistance(i, s, r, a);
}, convertMC2LL: function(t) {
  if (null == t)
    return new Point(0, 0);
  if (t.lng < 180 && t.lng > -180 && t.lat < 90 && t.lat > -90)
    return t;
  let e, i;
  e = new Point(Math.abs(t.lng), Math.abs(t.lat));
  for (let t2 = 0; t2 < this.MCBAND.length; t2++)
    if (e.lat >= this.MCBAND[t2]) {
      i = this.MC2LL[t2];
      break;
    }
  let s = this.convertor(t, i);
  return t = new Point(s.lng.toFixed(6), s.lat.toFixed(6));
}, convertLL2MC: function(t) {
  if (null == t)
    return new Point(0, 0);
  if (t.lng > 180 || t.lng < -180 || t.lat > 90 || t.lat < -90)
    return t;
  let e, i;
  t.lng = this.getLoop(t.lng, -180, 180), t.lat = this.getRange(t.lat, -74, 74), e = new Point(t.lng, t.lat);
  for (var s = 0; s < this.LLBAND.length; s++)
    if (e.lat >= this.LLBAND[s]) {
      i = this.LL2MC[s];
      break;
    }
  if (!i) {
    for (s = 0; s < this.LLBAND.length; s++)
      if (e.lat <= -this.LLBAND[s]) {
        i = this.LL2MC[s];
        break;
      }
  }
  let r = this.convertor(t, i);
  return t = new Point(Number(r.lng), Number(r.lat));
}, convertor: function(t, e) {
  if (!t || !e)
    return;
  let i = e[0] + e[1] * Math.abs(t.lng), s = Math.abs(t.lat) / e[9], r = e[2] + e[3] * s + e[4] * s * s + e[5] * s * s * s + e[6] * s * s * s * s + e[7] * s * s * s * s * s + e[8] * s * s * s * s * s * s;
  return i *= t.lng < 0 ? -1 : 1, r *= t.lat < 0 ? -1 : 1, new Point(i, r);
}, getDistance: function(t, e, i, s) {
  return this.EARTHRADIUS * Math.acos(Math.sin(i) * Math.sin(s) + Math.cos(i) * Math.cos(s) * Math.cos(e - t));
}, toRadians: function(t) {
  return Math.PI * t / 180;
}, toDegrees: function(t) {
  return 180 * t / Math.PI;
}, getRange: function(t, e, i) {
  return null != e && (t = Math.max(t, e)), null != i && (t = Math.min(t, i)), t;
}, getLoop: function(t, e, i) {
  for (; t > i; )
    t -= i - e;
  for (; t < e; )
    t += i - e;
  return t;
} }), extend(MercatorProjection.prototype, { lngLatToMercator: function(t) {
  return MercatorProjection.convertLL2MC(t);
}, lngLatToPoint: function(t) {
  let e = MercatorProjection.convertLL2MC(t);
  return new Pixel(e.lng, e.lat);
}, mercatorToLngLat: function(t) {
  return MercatorProjection.convertMC2LL(t);
}, pointToLngLat: function(t) {
  let e = new Point(t.x, t.y);
  return MercatorProjection.convertMC2LL(e);
}, pointToPixel: function(t, e, i, s, r) {
  if (!t)
    return;
  t = this.lngLatToMercator(t, r);
  let a = this.getZoomUnits(e);
  return new Pixel(Math.round((t.lng - i.lng) / a + s.width / 2), Math.round((i.lat - t.lat) / a + s.height / 2));
}, pixelToPoint: function(t, e, i, s, r) {
  if (!t)
    return;
  let a = this.getZoomUnits(e), n = new Point(i.lng + a * (t.x - s.width / 2), i.lat - a * (t.y - s.height / 2));
  return this.mercatorToLngLat(n, r);
}, getZoomUnits: function(t) {
  return Math.pow(2, 18 - t);
} });
const MAX_X = 2003772636e-2, MAX_Y = 1247410417e-2, MAX_LAT = 74, MIN_LAT = -MAX_LAT, _tempInput = new Vector3$1();
class BaiduMercatorProjection extends Projection {
  constructor() {
    super(...arguments);
    __publicField(this, "name", PROJECTION_BD_MERCATOR);
    __publicField(this, "isAxisAligned", true);
    __publicField(this, "unprojectCoordinate", (t, e, i) => {
      e || (e = new Vector3$1()), _tempInput.copy(t), t.x < -MAX_X && (_tempInput.x = -MAX_X), t.x > MAX_X && (_tempInput.x = MAX_X), t.y < -MAX_Y && (_tempInput.y = -MAX_Y), t.y > MAX_Y && (_tempInput.y = MAX_Y);
      const s = MercatorProjection.convertMC2LL({ lng: _tempInput.x, lat: _tempInput.y });
      return e.set(Number(s.lng), Number(s.lat), _tempInput.z), i && (e.x = extendUnprojectCoordinate(t.x, e.x, 180, MAX_X), e.y = extendUnprojectCoordinate(t.y, e.y, MAX_LAT, MAX_Y)), e;
    });
  }
  projectCoordinate(t, e, i = false) {
    e || (e = new Vector3$1()), _tempInput.copy(t), t.x < -180 && (_tempInput.x = -180), t.x > 180 && (_tempInput.x = 180), t.y < MIN_LAT && (_tempInput.y = MIN_LAT), t.y > MAX_LAT && (_tempInput.y = MAX_LAT);
    const s = MercatorProjection.convertLL2MC({ lng: _tempInput.x, lat: _tempInput.y });
    return e.set(Number(s.lng), Number(s.lat), _tempInput.z), i && (e.x = extendProjectCoordinate(t.x, e.x, 180, MAX_X), e.y = extendProjectCoordinate(t.y, e.y, MAX_LAT, MAX_Y)), e;
  }
}
const scaleFactor = 6378137 * Math.PI / 180;
class GeoProjection extends Projection {
  constructor() {
    super(...arguments);
    __publicField(this, "name", PROJECTION_GEO);
    __publicField(this, "isGeo", true);
    __publicField(this, "isAxisAligned", true);
  }
  projectCoordinate(t, e) {
    return e || (e = new Vector3$1()), e.x = t.x * scaleFactor, e.y = t.y * scaleFactor, e.z = t.z, e;
  }
  unprojectCoordinate(t, e) {
    return e || (e = new Vector3$1()), e.x = t.x / scaleFactor, e.y = t.y / scaleFactor, e.z = t.z, e;
  }
}
class ScreenPixelProjection extends Projection {
  constructor() {
    super(...arguments);
    __publicField(this, "name", PROJECTION_SCREEN_PIXEL);
    __publicField(this, "isAxisAligned", true);
  }
  projectCoordinate(t, e) {
    return e || (e = new Vector3$1()), e.x = t.x, e.y = -t.y, e.z = t.z, e;
  }
  unprojectCoordinate(t, e) {
    return e || (e = new Vector3$1()), e.x = t.x, e.y = -t.y, e.z = t.z, e;
  }
}
var proj4Src = { exports: {} };
proj4Src.exports = function() {
  function t(t2) {
    t2("EPSG:4326", "+title=WGS 84 (long/lat) +proj=longlat +ellps=WGS84 +datum=WGS84 +units=degrees"), t2("EPSG:4269", "+title=NAD83 (long/lat) +proj=longlat +a=6378137.0 +b=6356752.31414036 +ellps=GRS80 +datum=NAD83 +units=degrees"), t2("EPSG:3857", "+title=WGS 84 / Pseudo-Mercator +proj=merc +a=6378137 +b=6378137 +lat_ts=0.0 +lon_0=0.0 +x_0=0.0 +y_0=0 +k=1.0 +units=m +nadgrids=@null +no_defs");
    for (var e2 = 1; e2 <= 60; ++e2)
      t2("EPSG:" + (32600 + e2), "+proj=utm +zone=" + e2 + " +datum=WGS84 +units=m"), t2("EPSG:" + (32700 + e2), "+proj=utm +zone=" + e2 + " +south +datum=WGS84 +units=m");
    t2.WGS84 = t2["EPSG:4326"], t2["EPSG:3785"] = t2["EPSG:3857"], t2.GOOGLE = t2["EPSG:3857"], t2["EPSG:900913"] = t2["EPSG:3857"], t2["EPSG:102113"] = t2["EPSG:3857"];
  }
  var e = 1, i = 2, s = 3, r = 4, a = 5, n = 6378137, o = 6356752314e-3, h = 0.0066943799901413165, c = 484813681109536e-20, l = Math.PI / 2, u = 0.16666666666666666, d = 0.04722222222222222, m = 0.022156084656084655, _ = 1e-10, f = 0.017453292519943295, p = 57.29577951308232, x = Math.PI / 4, y = 2 * Math.PI, M = 3.14159265359, g = { greenwich: 0, lisbon: -9.131906111111, paris: 2.337229166667, bogota: -74.080916666667, madrid: -3.687938888889, rome: 12.452333333333, bern: 7.439583333333, jakarta: 106.807719444444, ferro: -17.666666666667, brussels: 4.367975, stockholm: 18.058277777778, athens: 23.7163375, oslo: 10.722916666667 }, w = { mm: { to_meter: 1e-3 }, cm: { to_meter: 0.01 }, ft: { to_meter: 0.3048 }, "us-ft": { to_meter: 1200 / 3937 }, fath: { to_meter: 1.8288 }, kmi: { to_meter: 1852 }, "us-ch": { to_meter: 20.1168402336805 }, "us-mi": { to_meter: 1609.34721869444 }, km: { to_meter: 1e3 }, "ind-ft": { to_meter: 0.30479841 }, "ind-yd": { to_meter: 0.91439523 }, mi: { to_meter: 1609.344 }, yd: { to_meter: 0.9144 }, ch: { to_meter: 20.1168 }, link: { to_meter: 0.201168 }, dm: { to_meter: 0.1 }, in: { to_meter: 0.0254 }, "ind-ch": { to_meter: 20.11669506 }, "us-in": { to_meter: 0.025400050800101 }, "us-yd": { to_meter: 0.914401828803658 } }, P = /[\s_\-\/\(\)]/g;
  function S(t2, e2) {
    if (t2[e2])
      return t2[e2];
    for (var i2, s2 = Object.keys(t2), r2 = e2.toLowerCase().replace(P, ""), a2 = -1; ++a2 < s2.length; )
      if ((i2 = s2[a2]).toLowerCase().replace(P, "") === r2)
        return t2[i2];
  }
  function C(t2) {
    var e2, i2, s2, r2 = {}, a2 = t2.split("+").map(function(t3) {
      return t3.trim();
    }).filter(function(t3) {
      return t3;
    }).reduce(function(t3, e3) {
      var i3 = e3.split("=");
      return i3.push(true), t3[i3[0].toLowerCase()] = i3[1], t3;
    }, {}), n2 = { proj: "projName", datum: "datumCode", rf: function(t3) {
      r2.rf = parseFloat(t3);
    }, lat_0: function(t3) {
      r2.lat0 = t3 * f;
    }, lat_1: function(t3) {
      r2.lat1 = t3 * f;
    }, lat_2: function(t3) {
      r2.lat2 = t3 * f;
    }, lat_ts: function(t3) {
      r2.lat_ts = t3 * f;
    }, lon_0: function(t3) {
      r2.long0 = t3 * f;
    }, lon_1: function(t3) {
      r2.long1 = t3 * f;
    }, lon_2: function(t3) {
      r2.long2 = t3 * f;
    }, alpha: function(t3) {
      r2.alpha = parseFloat(t3) * f;
    }, gamma: function(t3) {
      r2.rectified_grid_angle = parseFloat(t3) * f;
    }, lonc: function(t3) {
      r2.longc = t3 * f;
    }, x_0: function(t3) {
      r2.x0 = parseFloat(t3);
    }, y_0: function(t3) {
      r2.y0 = parseFloat(t3);
    }, k_0: function(t3) {
      r2.k0 = parseFloat(t3);
    }, k: function(t3) {
      r2.k0 = parseFloat(t3);
    }, a: function(t3) {
      r2.a = parseFloat(t3);
    }, b: function(t3) {
      r2.b = parseFloat(t3);
    }, r: function(t3) {
      r2.a = r2.b = parseFloat(t3);
    }, r_a: function() {
      r2.R_A = true;
    }, zone: function(t3) {
      r2.zone = parseInt(t3, 10);
    }, south: function() {
      r2.utmSouth = true;
    }, towgs84: function(t3) {
      r2.datum_params = t3.split(",").map(function(t4) {
        return parseFloat(t4);
      });
    }, to_meter: function(t3) {
      r2.to_meter = parseFloat(t3);
    }, units: function(t3) {
      r2.units = t3;
      var e3 = S(w, t3);
      e3 && (r2.to_meter = e3.to_meter);
    }, from_greenwich: function(t3) {
      r2.from_greenwich = t3 * f;
    }, pm: function(t3) {
      var e3 = S(g, t3);
      r2.from_greenwich = (e3 || parseFloat(t3)) * f;
    }, nadgrids: function(t3) {
      "@null" === t3 ? r2.datumCode = "none" : r2.nadgrids = t3;
    }, axis: function(t3) {
      var e3 = "ewnsud";
      3 === t3.length && -1 !== e3.indexOf(t3.substr(0, 1)) && -1 !== e3.indexOf(t3.substr(1, 1)) && -1 !== e3.indexOf(t3.substr(2, 1)) && (r2.axis = t3);
    }, approx: function() {
      r2.approx = true;
    } };
    for (e2 in a2)
      i2 = a2[e2], e2 in n2 ? "function" == typeof (s2 = n2[e2]) ? s2(i2) : r2[s2] = i2 : r2[e2] = i2;
    return "string" == typeof r2.datumCode && "WGS84" !== r2.datumCode && (r2.datumCode = r2.datumCode.toLowerCase()), r2;
  }
  class E {
    static getId(t2) {
      const e2 = t2.find((t3) => Array.isArray(t3) && "ID" === t3[0]);
      return e2 && e2.length >= 3 ? { authority: e2[1], code: parseInt(e2[2], 10) } : null;
    }
    static convertUnit(t2, e2 = "unit") {
      if (!t2 || t2.length < 3)
        return { type: e2, name: "unknown", conversion_factor: null };
      const i2 = t2[1], s2 = parseFloat(t2[2]) || null, r2 = t2.find((t3) => Array.isArray(t3) && "ID" === t3[0]);
      return { type: e2, name: i2, conversion_factor: s2, id: r2 ? { authority: r2[1], code: parseInt(r2[2], 10) } : null };
    }
    static convertAxis(t2) {
      var _a2;
      const e2 = t2[1] || "Unknown";
      let i2;
      const s2 = e2.match(/^\((.)\)$/);
      if (s2) {
        const t3 = s2[1].toUpperCase();
        if ("E" === t3)
          i2 = "east";
        else if ("N" === t3)
          i2 = "north";
        else {
          if ("U" !== t3)
            throw new Error(`Unknown axis abbreviation: ${t3}`);
          i2 = "up";
        }
      } else
        i2 = ((_a2 = t2[2]) == null ? void 0 : _a2.toLowerCase()) || "unknown";
      const r2 = t2.find((t3) => Array.isArray(t3) && "ORDER" === t3[0]), a2 = r2 ? parseInt(r2[1], 10) : null, n2 = t2.find((t3) => Array.isArray(t3) && ("LENGTHUNIT" === t3[0] || "ANGLEUNIT" === t3[0] || "SCALEUNIT" === t3[0]));
      return { name: e2, direction: i2, unit: this.convertUnit(n2), order: a2 };
    }
    static extractAxes(t2) {
      return t2.filter((t3) => Array.isArray(t3) && "AXIS" === t3[0]).map((t3) => this.convertAxis(t3)).sort((t3, e2) => (t3.order || 0) - (e2.order || 0));
    }
    static convert(t2, e2 = {}) {
      switch (t2[0]) {
        case "PROJCRS":
          e2.type = "ProjectedCRS", e2.name = t2[1], e2.base_crs = t2.find((t3) => Array.isArray(t3) && "BASEGEOGCRS" === t3[0]) ? this.convert(t2.find((t3) => Array.isArray(t3) && "BASEGEOGCRS" === t3[0])) : null, e2.conversion = t2.find((t3) => Array.isArray(t3) && "CONVERSION" === t3[0]) ? this.convert(t2.find((t3) => Array.isArray(t3) && "CONVERSION" === t3[0])) : null;
          const i2 = t2.find((t3) => Array.isArray(t3) && "CS" === t3[0]);
          i2 && (e2.coordinate_system = { type: i2[1], axis: this.extractAxes(t2) });
          const s2 = t2.find((t3) => Array.isArray(t3) && "LENGTHUNIT" === t3[0]);
          if (s2) {
            const t3 = this.convertUnit(s2);
            e2.coordinate_system.unit = t3;
          }
          e2.id = this.getId(t2);
          break;
        case "BASEGEOGCRS":
        case "GEOGCRS":
          e2.type = "GeographicCRS", e2.name = t2[1];
          const r2 = t2.find((t3) => Array.isArray(t3) && ("DATUM" === t3[0] || "ENSEMBLE" === t3[0]));
          if (r2) {
            const i3 = this.convert(r2);
            "ENSEMBLE" === r2[0] ? e2.datum_ensemble = i3 : e2.datum = i3;
            const s3 = t2.find((t3) => Array.isArray(t3) && "PRIMEM" === t3[0]);
            s3 && "Greenwich" !== s3[1] && (i3.prime_meridian = { name: s3[1], longitude: parseFloat(s3[2]) });
          }
          e2.coordinate_system = { type: "ellipsoidal", axis: this.extractAxes(t2) }, e2.id = this.getId(t2);
          break;
        case "DATUM":
          e2.type = "GeodeticReferenceFrame", e2.name = t2[1], e2.ellipsoid = t2.find((t3) => Array.isArray(t3) && "ELLIPSOID" === t3[0]) ? this.convert(t2.find((t3) => Array.isArray(t3) && "ELLIPSOID" === t3[0])) : null;
          break;
        case "ENSEMBLE":
          e2.type = "DatumEnsemble", e2.name = t2[1], e2.members = t2.filter((t3) => Array.isArray(t3) && "MEMBER" === t3[0]).map((t3) => ({ type: "DatumEnsembleMember", name: t3[1], id: this.getId(t3) }));
          const a2 = t2.find((t3) => Array.isArray(t3) && "ENSEMBLEACCURACY" === t3[0]);
          a2 && (e2.accuracy = parseFloat(a2[1]));
          const n2 = t2.find((t3) => Array.isArray(t3) && "ELLIPSOID" === t3[0]);
          n2 && (e2.ellipsoid = this.convert(n2)), e2.id = this.getId(t2);
          break;
        case "ELLIPSOID":
          e2.type = "Ellipsoid", e2.name = t2[1], e2.semi_major_axis = parseFloat(t2[2]), e2.inverse_flattening = parseFloat(t2[3]), t2.find((t3) => Array.isArray(t3) && "LENGTHUNIT" === t3[0]) && this.convert(t2.find((t3) => Array.isArray(t3) && "LENGTHUNIT" === t3[0]), e2);
          break;
        case "CONVERSION":
          e2.type = "Conversion", e2.name = t2[1], e2.method = t2.find((t3) => Array.isArray(t3) && "METHOD" === t3[0]) ? this.convert(t2.find((t3) => Array.isArray(t3) && "METHOD" === t3[0])) : null, e2.parameters = t2.filter((t3) => Array.isArray(t3) && "PARAMETER" === t3[0]).map((t3) => this.convert(t3));
          break;
        case "METHOD":
          e2.type = "Method", e2.name = t2[1], e2.id = this.getId(t2);
          break;
        case "PARAMETER":
          e2.type = "Parameter", e2.name = t2[1], e2.value = parseFloat(t2[2]), e2.unit = this.convertUnit(t2.find((t3) => Array.isArray(t3) && ("LENGTHUNIT" === t3[0] || "ANGLEUNIT" === t3[0] || "SCALEUNIT" === t3[0]))), e2.id = this.getId(t2);
          break;
        case "BOUNDCRS":
          e2.type = "BoundCRS";
          const o2 = t2.find((t3) => Array.isArray(t3) && "SOURCECRS" === t3[0]);
          if (o2) {
            const t3 = o2.find((t4) => Array.isArray(t4));
            e2.source_crs = t3 ? this.convert(t3) : null;
          }
          const h2 = t2.find((t3) => Array.isArray(t3) && "TARGETCRS" === t3[0]);
          if (h2) {
            const t3 = h2.find((t4) => Array.isArray(t4));
            e2.target_crs = t3 ? this.convert(t3) : null;
          }
          const c2 = t2.find((t3) => Array.isArray(t3) && "ABRIDGEDTRANSFORMATION" === t3[0]);
          e2.transformation = c2 ? this.convert(c2) : null;
          break;
        case "ABRIDGEDTRANSFORMATION":
          if (e2.type = "Transformation", e2.name = t2[1], e2.method = t2.find((t3) => Array.isArray(t3) && "METHOD" === t3[0]) ? this.convert(t2.find((t3) => Array.isArray(t3) && "METHOD" === t3[0])) : null, e2.parameters = t2.filter((t3) => Array.isArray(t3) && ("PARAMETER" === t3[0] || "PARAMETERFILE" === t3[0])).map((t3) => "PARAMETER" === t3[0] ? this.convert(t3) : "PARAMETERFILE" === t3[0] ? { name: t3[1], value: t3[2], id: { authority: "EPSG", code: 8656 } } : void 0), 7 === e2.parameters.length) {
            const t3 = e2.parameters[6];
            "Scale difference" === t3.name && (t3.value = Math.round(1e12 * (t3.value - 1)) / 1e6);
          }
          e2.id = this.getId(t2);
          break;
        case "AXIS":
          e2.coordinate_system || (e2.coordinate_system = { type: "unspecified", axis: [] }), e2.coordinate_system.axis.push(this.convertAxis(t2));
          break;
        case "LENGTHUNIT":
          const l2 = this.convertUnit(t2, "LinearUnit");
          e2.coordinate_system && e2.coordinate_system.axis && e2.coordinate_system.axis.forEach((t3) => {
            t3.unit || (t3.unit = l2);
          }), l2.conversion_factor && 1 !== l2.conversion_factor && e2.semi_major_axis && (e2.semi_major_axis = { value: e2.semi_major_axis, unit: l2 });
          break;
        default:
          e2.keyword = t2[0];
      }
      return e2;
    }
  }
  class v extends E {
    static convert(t2, e2 = {}) {
      var _a2;
      return super.convert(t2, e2), "Cartesian" === ((_a2 = e2.coordinate_system) == null ? void 0 : _a2.subtype) && delete e2.coordinate_system, e2.usage && delete e2.usage, e2;
    }
  }
  class b extends E {
    static convert(t2, e2 = {}) {
      var _a2, _b2, _c;
      super.convert(t2, e2);
      const i2 = t2.find((t3) => Array.isArray(t3) && "CS" === t3[0]);
      i2 && (e2.coordinate_system = { subtype: i2[1], axis: this.extractAxes(t2) });
      const s2 = t2.find((t3) => Array.isArray(t3) && "USAGE" === t3[0]);
      return s2 && (e2.usage = { scope: (_a2 = s2.find((t3) => Array.isArray(t3) && "SCOPE" === t3[0])) == null ? void 0 : _a2[1], area: (_b2 = s2.find((t3) => Array.isArray(t3) && "AREA" === t3[0])) == null ? void 0 : _b2[1], bbox: (_c = s2.find((t3) => Array.isArray(t3) && "BBOX" === t3[0])) == null ? void 0 : _c.slice(1) }), e2;
    }
  }
  function z(t2) {
    return t2.find((t3) => Array.isArray(t3) && "USAGE" === t3[0]) ? "2019" : (t2.find((t3) => Array.isArray(t3) && "CS" === t3[0]) || "BOUNDCRS" === t2[0] || "PROJCRS" === t2[0] || t2[0], "2015");
  }
  function T(t2) {
    return ("2019" === z(t2) ? b : v).convert(t2);
  }
  function A2(t2) {
    const e2 = t2.toUpperCase();
    return e2.includes("PROJCRS") || e2.includes("GEOGCRS") || e2.includes("BOUNDCRS") || e2.includes("VERTCRS") || e2.includes("LENGTHUNIT") || e2.includes("ANGLEUNIT") || e2.includes("SCALEUNIT") ? "WKT2" : (e2.includes("PROJCS") || e2.includes("GEOGCS") || e2.includes("LOCAL_CS") || e2.includes("VERT_CS") || e2.includes("UNIT"), "WKT1");
  }
  var O = 1, G = 2, N = 3, I = 4, R = 5, $ = -1, V = /\s/, L = /[A-Za-z]/, B = /[A-Za-z84_]/, q = /[,\]]/, j = /[\d\.E\-\+]/;
  function F(t2) {
    if ("string" != typeof t2)
      throw new Error("not a string");
    this.text = t2.trim(), this.level = 0, this.place = 0, this.root = null, this.stack = [], this.currentObject = null, this.state = O;
  }
  function k(t2) {
    return new F(t2).output();
  }
  function D(t2, e2, i2) {
    Array.isArray(e2) && (i2.unshift(e2), e2 = null);
    var s2 = e2 ? {} : t2, r2 = i2.reduce(function(t3, e3) {
      return U(e3, t3), t3;
    }, s2);
    e2 && (t2[e2] = r2);
  }
  function U(t2, e2) {
    if (Array.isArray(t2)) {
      var i2 = t2.shift();
      if ("PARAMETER" === i2 && (i2 = t2.shift()), 1 === t2.length)
        return Array.isArray(t2[0]) ? (e2[i2] = {}, void U(t2[0], e2[i2])) : void (e2[i2] = t2[0]);
      if (t2.length)
        if ("TOWGS84" !== i2) {
          if ("AXIS" === i2)
            return i2 in e2 || (e2[i2] = []), void e2[i2].push(t2);
          var s2;
          switch (Array.isArray(i2) || (e2[i2] = {}), i2) {
            case "UNIT":
            case "PRIMEM":
            case "VERT_DATUM":
              return e2[i2] = { name: t2[0].toLowerCase(), convert: t2[1] }, void (3 === t2.length && U(t2[2], e2[i2]));
            case "SPHEROID":
            case "ELLIPSOID":
              return e2[i2] = { name: t2[0], a: t2[1], rf: t2[2] }, void (4 === t2.length && U(t2[3], e2[i2]));
            case "EDATUM":
            case "ENGINEERINGDATUM":
            case "LOCAL_DATUM":
            case "DATUM":
            case "VERT_CS":
            case "VERTCRS":
            case "VERTICALCRS":
              return t2[0] = ["name", t2[0]], void D(e2, i2, t2);
            case "COMPD_CS":
            case "COMPOUNDCRS":
            case "FITTED_CS":
            case "PROJECTEDCRS":
            case "PROJCRS":
            case "GEOGCS":
            case "GEOCCS":
            case "PROJCS":
            case "LOCAL_CS":
            case "GEODCRS":
            case "GEODETICCRS":
            case "GEODETICDATUM":
            case "ENGCRS":
            case "ENGINEERINGCRS":
              return t2[0] = ["name", t2[0]], D(e2, i2, t2), void (e2[i2].type = i2);
            default:
              for (s2 = -1; ++s2 < t2.length; )
                if (!Array.isArray(t2[s2]))
                  return U(t2, e2[i2]);
              return D(e2, i2, t2);
          }
        } else
          e2[i2] = t2;
      else
        e2[i2] = true;
    } else
      e2[t2] = true;
  }
  F.prototype.readCharicter = function() {
    var t2 = this.text[this.place++];
    if (this.state !== I)
      for (; V.test(t2); ) {
        if (this.place >= this.text.length)
          return;
        t2 = this.text[this.place++];
      }
    switch (this.state) {
      case O:
        return this.neutral(t2);
      case G:
        return this.keyword(t2);
      case I:
        return this.quoted(t2);
      case R:
        return this.afterquote(t2);
      case N:
        return this.number(t2);
      case $:
        return;
    }
  }, F.prototype.afterquote = function(t2) {
    if ('"' === t2)
      return this.word += '"', void (this.state = I);
    if (q.test(t2))
      return this.word = this.word.trim(), void this.afterItem(t2);
    throw new Error(`havn't handled "` + t2 + '" in afterquote yet, index ' + this.place);
  }, F.prototype.afterItem = function(t2) {
    return "," === t2 ? (null !== this.word && this.currentObject.push(this.word), this.word = null, void (this.state = O)) : "]" === t2 ? (this.level--, null !== this.word && (this.currentObject.push(this.word), this.word = null), this.state = O, this.currentObject = this.stack.pop(), void (this.currentObject || (this.state = $))) : void 0;
  }, F.prototype.number = function(t2) {
    if (!j.test(t2)) {
      if (q.test(t2))
        return this.word = parseFloat(this.word), void this.afterItem(t2);
      throw new Error(`havn't handled "` + t2 + '" in number yet, index ' + this.place);
    }
    this.word += t2;
  }, F.prototype.quoted = function(t2) {
    '"' !== t2 ? this.word += t2 : this.state = R;
  }, F.prototype.keyword = function(t2) {
    if (B.test(t2))
      this.word += t2;
    else {
      if ("[" === t2) {
        var e2 = [];
        return e2.push(this.word), this.level++, null === this.root ? this.root = e2 : this.currentObject.push(e2), this.stack.push(this.currentObject), this.currentObject = e2, void (this.state = O);
      }
      if (!q.test(t2))
        throw new Error(`havn't handled "` + t2 + '" in keyword yet, index ' + this.place);
      this.afterItem(t2);
    }
  }, F.prototype.neutral = function(t2) {
    if (L.test(t2))
      return this.word = t2, void (this.state = G);
    if ('"' === t2)
      return this.word = "", void (this.state = I);
    if (j.test(t2))
      return this.word = t2, void (this.state = N);
    if (!q.test(t2))
      throw new Error(`havn't handled "` + t2 + '" in neutral yet, index ' + this.place);
    this.afterItem(t2);
  }, F.prototype.output = function() {
    for (; this.place < this.text.length; )
      this.readCharicter();
    if (this.state === $)
      return this.root;
    throw new Error('unable to parse string "' + this.text + '". State is ' + this.state);
  };
  var W = 0.017453292519943295;
  function X(t2) {
    return t2 * W;
  }
  function Q(t2) {
    const e2 = (t2.projName || "").toLowerCase().replace(/_/g, " ");
    t2.long0 || !t2.longc || "albers conic equal area" !== e2 && "lambert azimuthal equal area" !== e2 || (t2.long0 = t2.longc), t2.lat_ts || !t2.lat1 || "stereographic south pole" !== e2 && "polar stereographic (variant b)" !== e2 ? t2.lat_ts || !t2.lat0 || "polar stereographic" !== e2 && "polar stereographic (variant a)" !== e2 || (t2.lat_ts = t2.lat0, t2.lat0 = X(t2.lat0 > 0 ? 90 : -90), delete t2.lat1) : (t2.lat0 = X(t2.lat1 > 0 ? 90 : -90), t2.lat_ts = t2.lat1, delete t2.lat1);
  }
  function H(t2) {
    let e2 = { units: null, to_meter: void 0 };
    return "string" == typeof t2 ? (e2.units = t2.toLowerCase(), "metre" === e2.units && (e2.units = "meter"), "meter" === e2.units && (e2.to_meter = 1)) : (t2 == null ? void 0 : t2.name) && (e2.units = t2.name.toLowerCase(), "metre" === e2.units && (e2.units = "meter"), e2.to_meter = t2.conversion_factor), e2;
  }
  function Z(t2) {
    return "object" == typeof t2 ? t2.value * t2.unit.conversion_factor : t2;
  }
  function Y(t2, e2) {
    t2.ellipsoid.radius ? (e2.a = t2.ellipsoid.radius, e2.rf = 0) : (e2.a = Z(t2.ellipsoid.semi_major_axis), void 0 !== t2.ellipsoid.inverse_flattening ? e2.rf = t2.ellipsoid.inverse_flattening : void 0 !== t2.ellipsoid.semi_major_axis && void 0 !== t2.ellipsoid.semi_minor_axis && (e2.rf = e2.a / (e2.a - Z(t2.ellipsoid.semi_minor_axis))));
  }
  function J(t2, e2 = {}) {
    var _a2;
    return t2 && "object" == typeof t2 ? "BoundCRS" === t2.type ? (J(t2.source_crs, e2), t2.transformation && ("NTv2" === ((_a2 = t2.transformation.method) == null ? void 0 : _a2.name) ? e2.nadgrids = t2.transformation.parameters[0].value : e2.datum_params = t2.transformation.parameters.map((t3) => t3.value)), e2) : (Object.keys(t2).forEach((i2) => {
      var _a3, _b, _c;
      const s2 = t2[i2];
      if (null !== s2)
        switch (i2) {
          case "name":
            if (e2.srsCode)
              break;
            e2.name = s2, e2.srsCode = s2;
            break;
          case "type":
            "GeographicCRS" === s2 ? e2.projName = "longlat" : "ProjectedCRS" === s2 && (e2.projName = (_b = (_a3 = t2.conversion) == null ? void 0 : _a3.method) == null ? void 0 : _b.name);
            break;
          case "datum":
          case "datum_ensemble":
            s2.ellipsoid && (e2.ellps = s2.ellipsoid.name, Y(s2, e2)), s2.prime_meridian && (e2.from_greenwich = s2.prime_meridian.longitude * Math.PI / 180);
            break;
          case "ellipsoid":
            e2.ellps = s2.name, Y(s2, e2);
            break;
          case "prime_meridian":
            e2.long0 = (s2.longitude || 0) * Math.PI / 180;
            break;
          case "coordinate_system":
            if (s2.axis) {
              if (e2.axis = s2.axis.map((t3) => {
                const e3 = t3.direction;
                if ("east" === e3)
                  return "e";
                if ("north" === e3)
                  return "n";
                if ("west" === e3)
                  return "w";
                if ("south" === e3)
                  return "s";
                throw new Error(`Unknown axis direction: ${e3}`);
              }).join("") + "u", s2.unit) {
                const { units: t3, to_meter: i3 } = H(s2.unit);
                e2.units = t3, e2.to_meter = i3;
              } else if ((_c = s2.axis[0]) == null ? void 0 : _c.unit) {
                const { units: t3, to_meter: i3 } = H(s2.axis[0].unit);
                e2.units = t3, e2.to_meter = i3;
              }
            }
            break;
          case "id":
            s2.authority && s2.code && (e2.title = s2.authority + ":" + s2.code);
            break;
          case "conversion":
            s2.method && s2.method.name && (e2.projName = s2.method.name), s2.parameters && s2.parameters.forEach((t3) => {
              const i3 = t3.name.toLowerCase().replace(/\s+/g, "_"), s3 = t3.value;
              t3.unit && t3.unit.conversion_factor ? e2[i3] = s3 * t3.unit.conversion_factor : "degree" === t3.unit ? e2[i3] = s3 * Math.PI / 180 : e2[i3] = s3;
            });
            break;
          case "unit":
            s2.name && (e2.units = s2.name.toLowerCase(), "metre" === e2.units && (e2.units = "meter")), s2.conversion_factor && (e2.to_meter = s2.conversion_factor);
            break;
          case "base_crs":
            J(s2, e2), e2.datumCode = s2.id ? s2.id.authority + "_" + s2.id.code : s2.name;
        }
    }), void 0 !== e2.latitude_of_false_origin && (e2.lat0 = e2.latitude_of_false_origin), void 0 !== e2.longitude_of_false_origin && (e2.long0 = e2.longitude_of_false_origin), void 0 !== e2.latitude_of_standard_parallel && (e2.lat0 = e2.latitude_of_standard_parallel, e2.lat1 = e2.latitude_of_standard_parallel), void 0 !== e2.latitude_of_1st_standard_parallel && (e2.lat1 = e2.latitude_of_1st_standard_parallel), void 0 !== e2.latitude_of_2nd_standard_parallel && (e2.lat2 = e2.latitude_of_2nd_standard_parallel), void 0 !== e2.latitude_of_projection_centre && (e2.lat0 = e2.latitude_of_projection_centre), void 0 !== e2.longitude_of_projection_centre && (e2.longc = e2.longitude_of_projection_centre), void 0 !== e2.easting_at_false_origin && (e2.x0 = e2.easting_at_false_origin), void 0 !== e2.northing_at_false_origin && (e2.y0 = e2.northing_at_false_origin), void 0 !== e2.latitude_of_natural_origin && (e2.lat0 = e2.latitude_of_natural_origin), void 0 !== e2.longitude_of_natural_origin && (e2.long0 = e2.longitude_of_natural_origin), void 0 !== e2.longitude_of_origin && (e2.long0 = e2.longitude_of_origin), void 0 !== e2.false_easting && (e2.x0 = e2.false_easting), e2.easting_at_projection_centre && (e2.x0 = e2.easting_at_projection_centre), void 0 !== e2.false_northing && (e2.y0 = e2.false_northing), e2.northing_at_projection_centre && (e2.y0 = e2.northing_at_projection_centre), void 0 !== e2.standard_parallel_1 && (e2.lat1 = e2.standard_parallel_1), void 0 !== e2.standard_parallel_2 && (e2.lat2 = e2.standard_parallel_2), void 0 !== e2.scale_factor_at_natural_origin && (e2.k0 = e2.scale_factor_at_natural_origin), void 0 !== e2.scale_factor_at_projection_centre && (e2.k0 = e2.scale_factor_at_projection_centre), void 0 !== e2.scale_factor_on_pseudo_standard_parallel && (e2.k0 = e2.scale_factor_on_pseudo_standard_parallel), void 0 !== e2.azimuth && (e2.alpha = e2.azimuth), void 0 !== e2.azimuth_at_projection_centre && (e2.alpha = e2.azimuth_at_projection_centre), e2.angle_from_rectified_to_skew_grid && (e2.rectified_grid_angle = e2.angle_from_rectified_to_skew_grid), Q(e2), e2) : t2;
  }
  var K = ["PROJECTEDCRS", "PROJCRS", "GEOGCS", "GEOCCS", "PROJCS", "LOCAL_CS", "GEODCRS", "GEODETICCRS", "GEODETICDATUM", "ENGCRS", "ENGINEERINGCRS"];
  function tt(t2, e2) {
    var i2 = e2[0], s2 = e2[1];
    !(i2 in t2) && s2 in t2 && (t2[i2] = t2[s2], 3 === e2.length && (t2[i2] = e2[2](t2[i2])));
  }
  function et(t2) {
    for (var e2 = Object.keys(t2), i2 = 0, s2 = e2.length; i2 < s2; ++i2) {
      var r2 = e2[i2];
      -1 !== K.indexOf(r2) && it(t2[r2]), "object" == typeof t2[r2] && et(t2[r2]);
    }
  }
  function it(t2) {
    if (t2.AUTHORITY) {
      var e2 = Object.keys(t2.AUTHORITY)[0];
      e2 && e2 in t2.AUTHORITY && (t2.title = e2 + ":" + t2.AUTHORITY[e2]);
    }
    if ("GEOGCS" === t2.type ? t2.projName = "longlat" : "LOCAL_CS" === t2.type ? (t2.projName = "identity", t2.local = true) : "object" == typeof t2.PROJECTION ? t2.projName = Object.keys(t2.PROJECTION)[0] : t2.projName = t2.PROJECTION, t2.AXIS) {
      for (var i2 = "", s2 = 0, r2 = t2.AXIS.length; s2 < r2; ++s2) {
        var a2 = [t2.AXIS[s2][0].toLowerCase(), t2.AXIS[s2][1].toLowerCase()];
        -1 !== a2[0].indexOf("north") || ("y" === a2[0] || "lat" === a2[0]) && "north" === a2[1] ? i2 += "n" : -1 !== a2[0].indexOf("south") || ("y" === a2[0] || "lat" === a2[0]) && "south" === a2[1] ? i2 += "s" : -1 !== a2[0].indexOf("east") || ("x" === a2[0] || "lon" === a2[0]) && "east" === a2[1] ? i2 += "e" : -1 === a2[0].indexOf("west") && ("x" !== a2[0] && "lon" !== a2[0] || "west" !== a2[1]) || (i2 += "w");
      }
      2 === i2.length && (i2 += "u"), 3 === i2.length && (t2.axis = i2);
    }
    t2.UNIT && (t2.units = t2.UNIT.name.toLowerCase(), "metre" === t2.units && (t2.units = "meter"), t2.UNIT.convert && ("GEOGCS" === t2.type ? t2.DATUM && t2.DATUM.SPHEROID && (t2.to_meter = t2.UNIT.convert * t2.DATUM.SPHEROID.a) : t2.to_meter = t2.UNIT.convert));
    var n2 = t2.GEOGCS;
    function o2(e3) {
      return e3 * (t2.to_meter || 1);
    }
    "GEOGCS" === t2.type && (n2 = t2), n2 && (n2.DATUM ? t2.datumCode = n2.DATUM.name.toLowerCase() : t2.datumCode = n2.name.toLowerCase(), "d_" === t2.datumCode.slice(0, 2) && (t2.datumCode = t2.datumCode.slice(2)), "new_zealand_1949" === t2.datumCode && (t2.datumCode = "nzgd49"), "wgs_1984" !== t2.datumCode && "world_geodetic_system_1984" !== t2.datumCode || ("Mercator_Auxiliary_Sphere" === t2.PROJECTION && (t2.sphere = true), t2.datumCode = "wgs84"), "belge_1972" === t2.datumCode && (t2.datumCode = "rnb72"), n2.DATUM && n2.DATUM.SPHEROID && (t2.ellps = n2.DATUM.SPHEROID.name.replace("_19", "").replace(/[Cc]larke\_18/, "clrk"), "international" === t2.ellps.toLowerCase().slice(0, 13) && (t2.ellps = "intl"), t2.a = n2.DATUM.SPHEROID.a, t2.rf = parseFloat(n2.DATUM.SPHEROID.rf, 10)), n2.DATUM && n2.DATUM.TOWGS84 && (t2.datum_params = n2.DATUM.TOWGS84), ~t2.datumCode.indexOf("osgb_1936") && (t2.datumCode = "osgb36"), ~t2.datumCode.indexOf("osni_1952") && (t2.datumCode = "osni52"), (~t2.datumCode.indexOf("tm65") || ~t2.datumCode.indexOf("geodetic_datum_of_1965")) && (t2.datumCode = "ire65"), "ch1903+" === t2.datumCode && (t2.datumCode = "ch1903"), ~t2.datumCode.indexOf("israel") && (t2.datumCode = "isr93")), t2.b && !isFinite(t2.b) && (t2.b = t2.a), t2.rectified_grid_angle && (t2.rectified_grid_angle = X(t2.rectified_grid_angle)), [["standard_parallel_1", "Standard_Parallel_1"], ["standard_parallel_1", "Latitude of 1st standard parallel"], ["standard_parallel_2", "Standard_Parallel_2"], ["standard_parallel_2", "Latitude of 2nd standard parallel"], ["false_easting", "False_Easting"], ["false_easting", "False easting"], ["false-easting", "Easting at false origin"], ["false_northing", "False_Northing"], ["false_northing", "False northing"], ["false_northing", "Northing at false origin"], ["central_meridian", "Central_Meridian"], ["central_meridian", "Longitude of natural origin"], ["central_meridian", "Longitude of false origin"], ["latitude_of_origin", "Latitude_Of_Origin"], ["latitude_of_origin", "Central_Parallel"], ["latitude_of_origin", "Latitude of natural origin"], ["latitude_of_origin", "Latitude of false origin"], ["scale_factor", "Scale_Factor"], ["k0", "scale_factor"], ["latitude_of_center", "Latitude_Of_Center"], ["latitude_of_center", "Latitude_of_center"], ["lat0", "latitude_of_center", X], ["longitude_of_center", "Longitude_Of_Center"], ["longitude_of_center", "Longitude_of_center"], ["longc", "longitude_of_center", X], ["x0", "false_easting", o2], ["y0", "false_northing", o2], ["long0", "central_meridian", X], ["lat0", "latitude_of_origin", X], ["lat0", "standard_parallel_1", X], ["lat1", "standard_parallel_1", X], ["lat2", "standard_parallel_2", X], ["azimuth", "Azimuth"], ["alpha", "azimuth", X], ["srsCode", "name"]].forEach(function(e3) {
      return tt(t2, e3);
    }), Q(t2);
  }
  function st(t2) {
    if ("object" == typeof t2)
      return J(t2);
    const e2 = A2(t2);
    var i2 = k(t2);
    if ("WKT2" === e2)
      return J(T(i2));
    var s2 = i2[0], r2 = {};
    return U(i2, r2), et(r2), r2[s2];
  }
  function rt(t2) {
    var e2 = this;
    if (2 === arguments.length) {
      var i2 = arguments[1];
      "string" == typeof i2 ? "+" === i2.charAt(0) ? rt[t2] = C(arguments[1]) : rt[t2] = st(arguments[1]) : rt[t2] = i2;
    } else if (1 === arguments.length) {
      if (Array.isArray(t2))
        return t2.map(function(t3) {
          return Array.isArray(t3) ? rt.apply(e2, t3) : rt(t3);
        });
      if ("string" == typeof t2) {
        if (t2 in rt)
          return rt[t2];
      } else
        "EPSG" in t2 ? rt["EPSG:" + t2.EPSG] = t2 : "ESRI" in t2 ? rt["ESRI:" + t2.ESRI] = t2 : "IAU2000" in t2 ? rt["IAU2000:" + t2.IAU2000] = t2 : console.log(t2);
      return;
    }
  }
  function at(t2) {
    return "string" == typeof t2;
  }
  function nt(t2) {
    return t2 in rt;
  }
  function ot(t2) {
    return 0 !== t2.indexOf("+") && -1 !== t2.indexOf("[") || "object" == typeof t2 && !("srsCode" in t2);
  }
  t(rt);
  var ht = ["3857", "900913", "3785", "102113"];
  function ct(t2) {
    var e2 = S(t2, "authority");
    if (e2) {
      var i2 = S(e2, "epsg");
      return i2 && ht.indexOf(i2) > -1;
    }
  }
  function lt(t2) {
    var e2 = S(t2, "extension");
    if (e2)
      return S(e2, "proj4");
  }
  function ut(t2) {
    return "+" === t2[0];
  }
  function dt(t2) {
    if (!at(t2))
      return "projName" in t2 ? t2 : st(t2);
    if (nt(t2))
      return rt[t2];
    if (ot(t2)) {
      var e2 = st(t2);
      if (ct(e2))
        return rt["EPSG:3857"];
      var i2 = lt(e2);
      return i2 ? C(i2) : e2;
    }
    return ut(t2) ? C(t2) : void 0;
  }
  function mt(t2, e2) {
    var i2, s2;
    if (t2 = t2 || {}, !e2)
      return t2;
    for (s2 in e2)
      void 0 !== (i2 = e2[s2]) && (t2[s2] = i2);
    return t2;
  }
  function _t(t2, e2, i2) {
    var s2 = t2 * e2;
    return i2 / Math.sqrt(1 - s2 * s2);
  }
  function ft(t2) {
    return t2 < 0 ? -1 : 1;
  }
  function pt(t2) {
    return Math.abs(t2) <= M ? t2 : t2 - ft(t2) * y;
  }
  function xt(t2, e2, i2) {
    var s2 = t2 * i2, r2 = 0.5 * t2;
    return s2 = Math.pow((1 - s2) / (1 + s2), r2), Math.tan(0.5 * (l - e2)) / s2;
  }
  function yt(t2, e2) {
    for (var i2, s2, r2 = 0.5 * t2, a2 = l - 2 * Math.atan(e2), n2 = 0; n2 <= 15; n2++)
      if (i2 = t2 * Math.sin(a2), a2 += s2 = l - 2 * Math.atan(e2 * Math.pow((1 - i2) / (1 + i2), r2)) - a2, Math.abs(s2) <= 1e-10)
        return a2;
    return -9999;
  }
  function Mt() {
    var t2 = this.b / this.a;
    this.es = 1 - t2 * t2, "x0" in this || (this.x0 = 0), "y0" in this || (this.y0 = 0), this.e = Math.sqrt(this.es), this.lat_ts ? this.sphere ? this.k0 = Math.cos(this.lat_ts) : this.k0 = _t(this.e, Math.sin(this.lat_ts), Math.cos(this.lat_ts)) : this.k0 || (this.k ? this.k0 = this.k : this.k0 = 1);
  }
  function gt(t2) {
    var e2, i2, s2 = t2.x, r2 = t2.y;
    if (r2 * p > 90 && r2 * p < -90 && s2 * p > 180 && s2 * p < -180)
      return null;
    if (Math.abs(Math.abs(r2) - l) <= _)
      return null;
    if (this.sphere)
      e2 = this.x0 + this.a * this.k0 * pt(s2 - this.long0), i2 = this.y0 + this.a * this.k0 * Math.log(Math.tan(x + 0.5 * r2));
    else {
      var a2 = Math.sin(r2), n2 = xt(this.e, r2, a2);
      e2 = this.x0 + this.a * this.k0 * pt(s2 - this.long0), i2 = this.y0 - this.a * this.k0 * Math.log(n2);
    }
    return t2.x = e2, t2.y = i2, t2;
  }
  function wt(t2) {
    var e2, i2, s2 = t2.x - this.x0, r2 = t2.y - this.y0;
    if (this.sphere)
      i2 = l - 2 * Math.atan(Math.exp(-r2 / (this.a * this.k0)));
    else {
      var a2 = Math.exp(-r2 / (this.a * this.k0));
      if (-9999 === (i2 = yt(this.e, a2)))
        return null;
    }
    return e2 = pt(this.long0 + s2 / (this.a * this.k0)), t2.x = e2, t2.y = i2, t2;
  }
  function Pt() {
  }
  function St(t2) {
    return t2;
  }
  var Ct = [{ init: Mt, forward: gt, inverse: wt, names: ["Mercator", "Popular Visualisation Pseudo Mercator", "Mercator_1SP", "Mercator_Auxiliary_Sphere", "Mercator_Variant_A", "merc"] }, { init: Pt, forward: St, inverse: St, names: ["longlat", "identity"] }], Et = {}, vt = [];
  function bt(t2, e2) {
    var i2 = vt.length;
    return t2.names ? (vt[i2] = t2, t2.names.forEach(function(t3) {
      Et[t3.toLowerCase()] = i2;
    }), this) : (console.log(e2), true);
  }
  function zt(t2) {
    return t2.replace(/[-\(\)\s]+/g, " ").trim().replace(/ /g, "_");
  }
  function Tt(t2) {
    if (!t2)
      return false;
    var e2 = t2.toLowerCase();
    return void 0 !== Et[e2] && vt[Et[e2]] || (e2 = zt(e2)) in Et && vt[Et[e2]] ? vt[Et[e2]] : void 0;
  }
  function At() {
    Ct.forEach(bt);
  }
  var Ot = { start: At, add: bt, get: Tt }, Gt = { MERIT: { a: 6378137, rf: 298.257, ellipseName: "MERIT 1983" }, SGS85: { a: 6378136, rf: 298.257, ellipseName: "Soviet Geodetic System 85" }, GRS80: { a: 6378137, rf: 298.257222101, ellipseName: "GRS 1980(IUGG, 1980)" }, IAU76: { a: 6378140, rf: 298.257, ellipseName: "IAU 1976" }, airy: { a: 6377563396e-3, b: 635625691e-2, ellipseName: "Airy 1830" }, APL4: { a: 6378137, rf: 298.25, ellipseName: "Appl. Physics. 1965" }, NWL9D: { a: 6378145, rf: 298.25, ellipseName: "Naval Weapons Lab., 1965" }, mod_airy: { a: 6377340189e-3, b: 6356034446e-3, ellipseName: "Modified Airy" }, andrae: { a: 637710443e-2, rf: 300, ellipseName: "Andrae 1876 (Den., Iclnd.)" }, aust_SA: { a: 6378160, rf: 298.25, ellipseName: "Australian Natl & S. Amer. 1969" }, GRS67: { a: 6378160, rf: 298.247167427, ellipseName: "GRS 67(IUGG 1967)" }, bessel: { a: 6377397155e-3, rf: 299.1528128, ellipseName: "Bessel 1841" }, bess_nam: { a: 6377483865e-3, rf: 299.1528128, ellipseName: "Bessel 1841 (Namibia)" }, clrk66: { a: 63782064e-1, b: 63565838e-1, ellipseName: "Clarke 1866" }, clrk80: { a: 6378249145e-3, rf: 293.4663, ellipseName: "Clarke 1880 mod." }, clrk80ign: { a: 63782492e-1, b: 6356515, rf: 293.4660213, ellipseName: "Clarke 1880 (IGN)" }, clrk58: { a: 6378293645208759e-9, rf: 294.2606763692654, ellipseName: "Clarke 1858" }, CPM: { a: 63757387e-1, rf: 334.29, ellipseName: "Comm. des Poids et Mesures 1799" }, delmbr: { a: 6376428, rf: 311.5, ellipseName: "Delambre 1810 (Belgium)" }, engelis: { a: 637813605e-2, rf: 298.2566, ellipseName: "Engelis 1985" }, evrst30: { a: 6377276345e-3, rf: 300.8017, ellipseName: "Everest 1830" }, evrst48: { a: 6377304063e-3, rf: 300.8017, ellipseName: "Everest 1948" }, evrst56: { a: 6377301243e-3, rf: 300.8017, ellipseName: "Everest 1956" }, evrst69: { a: 6377295664e-3, rf: 300.8017, ellipseName: "Everest 1969" }, evrstSS: { a: 6377298556e-3, rf: 300.8017, ellipseName: "Everest (Sabah & Sarawak)" }, fschr60: { a: 6378166, rf: 298.3, ellipseName: "Fischer (Mercury Datum) 1960" }, fschr60m: { a: 6378155, rf: 298.3, ellipseName: "Fischer 1960" }, fschr68: { a: 6378150, rf: 298.3, ellipseName: "Fischer 1968" }, helmert: { a: 6378200, rf: 298.3, ellipseName: "Helmert 1906" }, hough: { a: 6378270, rf: 297, ellipseName: "Hough" }, intl: { a: 6378388, rf: 297, ellipseName: "International 1909 (Hayford)" }, kaula: { a: 6378163, rf: 298.24, ellipseName: "Kaula 1961" }, lerch: { a: 6378139, rf: 298.257, ellipseName: "Lerch 1979" }, mprts: { a: 6397300, rf: 191, ellipseName: "Maupertius 1738" }, new_intl: { a: 63781575e-1, b: 63567722e-1, ellipseName: "New International 1967" }, plessis: { a: 6376523, rf: 6355863, ellipseName: "Plessis 1817 (France)" }, krass: { a: 6378245, rf: 298.3, ellipseName: "Krassovsky, 1942" }, SEasia: { a: 6378155, b: 63567733205e-4, ellipseName: "Southeast Asia" }, walbeck: { a: 6376896, b: 63558348467e-4, ellipseName: "Walbeck" }, WGS60: { a: 6378165, rf: 298.3, ellipseName: "WGS 60" }, WGS66: { a: 6378145, rf: 298.25, ellipseName: "WGS 66" }, WGS7: { a: 6378135, rf: 298.26, ellipseName: "WGS 72" }, WGS84: { a: 6378137, rf: 298.257223563, ellipseName: "WGS 84" }, sphere: { a: 6370997, b: 6370997, ellipseName: "Normal Sphere (r=6370997)" } };
  const Nt = Gt.WGS84;
  function It(t2, e2, i2, s2) {
    var r2 = t2 * t2, a2 = e2 * e2, n2 = (r2 - a2) / r2, o2 = 0;
    return s2 ? (r2 = (t2 *= 1 - n2 * (u + n2 * (d + n2 * m))) * t2, n2 = 0) : o2 = Math.sqrt(n2), { es: n2, e: o2, ep2: (r2 - a2) / a2 };
  }
  function Rt(t2, e2, i2, s2, r2) {
    if (!t2) {
      var a2 = S(Gt, s2);
      a2 || (a2 = Nt), t2 = a2.a, e2 = a2.b, i2 = a2.rf;
    }
    return i2 && !e2 && (e2 = (1 - 1 / i2) * t2), (0 === i2 || Math.abs(t2 - e2) < _) && (r2 = true, e2 = t2), { a: t2, b: e2, rf: i2, sphere: r2 };
  }
  var $t = { wgs84: { towgs84: "0,0,0", ellipse: "WGS84", datumName: "WGS84" }, ch1903: { towgs84: "674.374,15.056,405.346", ellipse: "bessel", datumName: "swiss" }, ggrs87: { towgs84: "-199.87,74.79,246.62", ellipse: "GRS80", datumName: "Greek_Geodetic_Reference_System_1987" }, nad83: { towgs84: "0,0,0", ellipse: "GRS80", datumName: "North_American_Datum_1983" }, nad27: { nadgrids: "@conus,@alaska,@ntv2_0.gsb,@ntv1_can.dat", ellipse: "clrk66", datumName: "North_American_Datum_1927" }, potsdam: { towgs84: "598.1,73.7,418.2,0.202,0.045,-2.455,6.7", ellipse: "bessel", datumName: "Potsdam Rauenberg 1950 DHDN" }, carthage: { towgs84: "-263.0,6.0,431.0", ellipse: "clark80", datumName: "Carthage 1934 Tunisia" }, hermannskogel: { towgs84: "577.326,90.129,463.919,5.137,1.474,5.297,2.4232", ellipse: "bessel", datumName: "Hermannskogel" }, mgi: { towgs84: "577.326,90.129,463.919,5.137,1.474,5.297,2.4232", ellipse: "bessel", datumName: "Militar-Geographische Institut" }, osni52: { towgs84: "482.530,-130.596,564.557,-1.042,-0.214,-0.631,8.15", ellipse: "airy", datumName: "Irish National" }, ire65: { towgs84: "482.530,-130.596,564.557,-1.042,-0.214,-0.631,8.15", ellipse: "mod_airy", datumName: "Ireland 1965" }, rassadiran: { towgs84: "-133.63,-157.5,-158.62", ellipse: "intl", datumName: "Rassadiran" }, nzgd49: { towgs84: "59.47,-5.04,187.44,0.47,-0.1,1.024,-4.5993", ellipse: "intl", datumName: "New Zealand Geodetic Datum 1949" }, osgb36: { towgs84: "446.448,-125.157,542.060,0.1502,0.2470,0.8421,-20.4894", ellipse: "airy", datumName: "Ordnance Survey of Great Britain 1936" }, s_jtsk: { towgs84: "589,76,480", ellipse: "bessel", datumName: "S-JTSK (Ferro)" }, beduaram: { towgs84: "-106,-87,188", ellipse: "clrk80", datumName: "Beduaram" }, gunung_segara: { towgs84: "-403,684,41", ellipse: "bessel", datumName: "Gunung Segara Jakarta" }, rnb72: { towgs84: "106.869,-52.2978,103.724,-0.33657,0.456955,-1.84218,1", ellipse: "intl", datumName: "Reseau National Belge 1972" }, EPSG_5451: { towgs84: "6.41,-49.05,-11.28,1.5657,0.5242,6.9718,-5.7649" }, IGNF_LURESG: { towgs84: "-192.986,13.673,-39.309,-0.4099,-2.9332,2.6881,0.43" }, EPSG_4614: { towgs84: "-119.4248,-303.65872,-11.00061,1.164298,0.174458,1.096259,3.657065" }, EPSG_4615: { towgs84: "-494.088,-312.129,279.877,-1.423,-1.013,1.59,-0.748" }, ESRI_37241: { towgs84: "-76.822,257.457,-12.817,2.136,-0.033,-2.392,-0.031" }, ESRI_37249: { towgs84: "-440.296,58.548,296.265,1.128,10.202,4.559,-0.438" }, ESRI_37245: { towgs84: "-511.151,-181.269,139.609,1.05,2.703,1.798,3.071" }, EPSG_4178: { towgs84: "24.9,-126.4,-93.2,-0.063,-0.247,-0.041,1.01" }, EPSG_4622: { towgs84: "-472.29,-5.63,-304.12,0.4362,-0.8374,0.2563,1.8984" }, EPSG_4625: { towgs84: "126.93,547.94,130.41,-2.7867,5.1612,-0.8584,13.8227" }, EPSG_5252: { towgs84: "0.023,0.036,-0.068,0.00176,0.00912,-0.01136,0.00439" }, EPSG_4314: { towgs84: "597.1,71.4,412.1,0.894,0.068,-1.563,7.58" }, EPSG_4282: { towgs84: "-178.3,-316.7,-131.5,5.278,6.077,10.979,19.166" }, EPSG_4231: { towgs84: "-83.11,-97.38,-117.22,0.0276,-0.2167,0.2147,0.1218" }, EPSG_4274: { towgs84: "-230.994,102.591,25.199,0.633,-0.239,0.9,1.95" }, EPSG_4134: { towgs84: "-180.624,-225.516,173.919,-0.81,-1.898,8.336,16.71006" }, EPSG_4254: { towgs84: "18.38,192.45,96.82,0.056,-0.142,-0.2,-0.0013" }, EPSG_4159: { towgs84: "-194.513,-63.978,-25.759,-3.4027,3.756,-3.352,-0.9175" }, EPSG_4687: { towgs84: "0.072,-0.507,-0.245,0.0183,-0.0003,0.007,-0.0093" }, EPSG_4227: { towgs84: "-83.58,-397.54,458.78,-17.595,-2.847,4.256,3.225" }, EPSG_4746: { towgs84: "599.4,72.4,419.2,-0.062,-0.022,-2.723,6.46" }, EPSG_4745: { towgs84: "612.4,77,440.2,-0.054,0.057,-2.797,2.55" }, EPSG_6311: { towgs84: "8.846,-4.394,-1.122,-0.00237,-0.146528,0.130428,0.783926" }, EPSG_4289: { towgs84: "565.7381,50.4018,465.2904,-1.91514,1.60363,-9.09546,4.07244" }, EPSG_4230: { towgs84: "-68.863,-134.888,-111.49,-0.53,-0.14,0.57,-3.4" }, EPSG_4154: { towgs84: "-123.02,-158.95,-168.47" }, EPSG_4156: { towgs84: "570.8,85.7,462.8,4.998,1.587,5.261,3.56" }, EPSG_4299: { towgs84: "482.5,-130.6,564.6,-1.042,-0.214,-0.631,8.15" }, EPSG_4179: { towgs84: "33.4,-146.6,-76.3,-0.359,-0.053,0.844,-0.84" }, EPSG_4313: { towgs84: "-106.8686,52.2978,-103.7239,0.3366,-0.457,1.8422,-1.2747" }, EPSG_4194: { towgs84: "163.511,127.533,-159.789" }, EPSG_4195: { towgs84: "105,326,-102.5" }, EPSG_4196: { towgs84: "-45,417,-3.5" }, EPSG_4611: { towgs84: "-162.619,-276.959,-161.764,0.067753,-2.243649,-1.158827,-1.094246" }, EPSG_4633: { towgs84: "137.092,131.66,91.475,-1.9436,-11.5993,-4.3321,-7.4824" }, EPSG_4641: { towgs84: "-408.809,366.856,-412.987,1.8842,-0.5308,2.1655,-121.0993" }, EPSG_4643: { towgs84: "-480.26,-438.32,-643.429,16.3119,20.1721,-4.0349,-111.7002" }, EPSG_4300: { towgs84: "482.5,-130.6,564.6,-1.042,-0.214,-0.631,8.15" }, EPSG_4188: { towgs84: "482.5,-130.6,564.6,-1.042,-0.214,-0.631,8.15" }, EPSG_4660: { towgs84: "982.6087,552.753,-540.873,32.39344,-153.25684,-96.2266,16.805" }, EPSG_4662: { towgs84: "97.295,-263.247,310.882,-1.5999,0.8386,3.1409,13.3259" }, EPSG_3906: { towgs84: "577.88891,165.22205,391.18289,4.9145,-0.94729,-13.05098,7.78664" }, EPSG_4307: { towgs84: "-209.3622,-87.8162,404.6198,0.0046,3.4784,0.5805,-1.4547" }, EPSG_6892: { towgs84: "-76.269,-16.683,68.562,-6.275,10.536,-4.286,-13.686" }, EPSG_4690: { towgs84: "221.597,152.441,176.523,2.403,1.3893,0.884,11.4648" }, EPSG_4691: { towgs84: "218.769,150.75,176.75,3.5231,2.0037,1.288,10.9817" }, EPSG_4629: { towgs84: "72.51,345.411,79.241,-1.5862,-0.8826,-0.5495,1.3653" }, EPSG_4630: { towgs84: "165.804,216.213,180.26,-0.6251,-0.4515,-0.0721,7.4111" }, EPSG_4692: { towgs84: "217.109,86.452,23.711,0.0183,-0.0003,0.007,-0.0093" }, EPSG_9333: { towgs84: "0,0,0,-8.393,0.749,-10.276,0" }, EPSG_9059: { towgs84: "0,0,0" }, EPSG_4312: { towgs84: "601.705,84.263,485.227,4.7354,1.3145,5.393,-2.3887" }, EPSG_4123: { towgs84: "-96.062,-82.428,-121.753,4.801,0.345,-1.376,1.496" }, EPSG_4309: { towgs84: "-124.45,183.74,44.64,-0.4384,0.5446,-0.9706,-2.1365" }, ESRI_104106: { towgs84: "-283.088,-70.693,117.445,-1.157,0.059,-0.652,-4.058" }, EPSG_4281: { towgs84: "-219.247,-73.802,269.529" }, EPSG_4322: { towgs84: "0,0,4.5" }, EPSG_4324: { towgs84: "0,0,1.9" }, EPSG_4284: { towgs84: "43.822,-108.842,-119.585,1.455,-0.761,0.737,0.549" }, EPSG_4277: { towgs84: "446.448,-125.157,542.06,0.15,0.247,0.842,-20.489" }, EPSG_4207: { towgs84: "-282.1,-72.2,120,-1.529,0.145,-0.89,-4.46" }, EPSG_4688: { towgs84: "347.175,1077.618,2623.677,33.9058,-70.6776,9.4013,186.0647" }, EPSG_4689: { towgs84: "410.793,54.542,80.501,-2.5596,-2.3517,-0.6594,17.3218" }, EPSG_4720: { towgs84: "0,0,4.5" }, EPSG_4273: { towgs84: "278.3,93,474.5,7.889,0.05,-6.61,6.21" }, EPSG_4240: { towgs84: "204.64,834.74,293.8" }, EPSG_4817: { towgs84: "278.3,93,474.5,7.889,0.05,-6.61,6.21" }, ESRI_104131: { towgs84: "426.62,142.62,460.09,4.98,4.49,-12.42,-17.1" }, EPSG_4265: { towgs84: "-104.1,-49.1,-9.9,0.971,-2.917,0.714,-11.68" }, EPSG_4263: { towgs84: "-111.92,-87.85,114.5,1.875,0.202,0.219,0.032" }, EPSG_4298: { towgs84: "-689.5937,623.84046,-65.93566,-0.02331,1.17094,-0.80054,5.88536" }, EPSG_4270: { towgs84: "-253.4392,-148.452,386.5267,0.15605,0.43,-0.1013,-0.0424" }, EPSG_4229: { towgs84: "-121.8,98.1,-10.7" }, EPSG_4220: { towgs84: "-55.5,-348,-229.2" }, EPSG_4214: { towgs84: "12.646,-155.176,-80.863" }, EPSG_4232: { towgs84: "-345,3,223" }, EPSG_4238: { towgs84: "-1.977,-13.06,-9.993,0.364,0.254,0.689,-1.037" }, EPSG_4168: { towgs84: "-170,33,326" }, EPSG_4131: { towgs84: "199,931,318.9" }, EPSG_4152: { towgs84: "-0.9102,2.0141,0.5602,0.029039,0.010065,0.010101,0" }, EPSG_5228: { towgs84: "572.213,85.334,461.94,4.9732,1.529,5.2484,3.5378" }, EPSG_8351: { towgs84: "485.021,169.465,483.839,7.786342,4.397554,4.102655,0" }, EPSG_4683: { towgs84: "-127.62,-67.24,-47.04,-3.068,4.903,1.578,-1.06" }, EPSG_4133: { towgs84: "0,0,0" }, EPSG_7373: { towgs84: "0.819,-0.5762,-1.6446,-0.00378,-0.03317,0.00318,0.0693" }, EPSG_9075: { towgs84: "-0.9102,2.0141,0.5602,0.029039,0.010065,0.010101,0" }, EPSG_9072: { towgs84: "-0.9102,2.0141,0.5602,0.029039,0.010065,0.010101,0" }, EPSG_9294: { towgs84: "1.16835,-1.42001,-2.24431,-0.00822,-0.05508,0.01818,0.23388" }, EPSG_4212: { towgs84: "-267.434,173.496,181.814,-13.4704,8.7154,7.3926,14.7492" }, EPSG_4191: { towgs84: "-44.183,-0.58,-38.489,2.3867,2.7072,-3.5196,-8.2703" }, EPSG_4237: { towgs84: "52.684,-71.194,-13.975,-0.312,-0.1063,-0.3729,1.0191" }, EPSG_4740: { towgs84: "-1.08,-0.27,-0.9" }, EPSG_4124: { towgs84: "419.3836,99.3335,591.3451,0.850389,1.817277,-7.862238,-0.99496" }, EPSG_5681: { towgs84: "584.9636,107.7175,413.8067,1.1155,0.2824,-3.1384,7.9922" }, EPSG_4141: { towgs84: "23.772,17.49,17.859,-0.3132,-1.85274,1.67299,-5.4262" }, EPSG_4204: { towgs84: "-85.645,-273.077,-79.708,2.289,-1.421,2.532,3.194" }, EPSG_4319: { towgs84: "226.702,-193.337,-35.371,-2.229,-4.391,9.238,0.9798" }, EPSG_4200: { towgs84: "24.82,-131.21,-82.66" }, EPSG_4130: { towgs84: "0,0,0" }, EPSG_4127: { towgs84: "-82.875,-57.097,-156.768,-2.158,1.524,-0.982,-0.359" }, EPSG_4149: { towgs84: "674.374,15.056,405.346" }, EPSG_4617: { towgs84: "-0.991,1.9072,0.5129,1.25033e-7,4.6785e-8,5.6529e-8,0" }, EPSG_4663: { towgs84: "-210.502,-66.902,-48.476,2.094,-15.067,-5.817,0.485" }, EPSG_4664: { towgs84: "-211.939,137.626,58.3,-0.089,0.251,0.079,0.384" }, EPSG_4665: { towgs84: "-105.854,165.589,-38.312,-0.003,-0.026,0.024,-0.048" }, EPSG_4666: { towgs84: "631.392,-66.551,481.442,1.09,-4.445,-4.487,-4.43" }, EPSG_4756: { towgs84: "-192.873,-39.382,-111.202,-0.00205,-0.0005,0.00335,0.0188" }, EPSG_4723: { towgs84: "-179.483,-69.379,-27.584,-7.862,8.163,6.042,-13.925" }, EPSG_4726: { towgs84: "8.853,-52.644,180.304,-0.393,-2.323,2.96,-24.081" }, EPSG_4267: { towgs84: "-8.0,160.0,176.0" }, EPSG_5365: { towgs84: "-0.16959,0.35312,0.51846,0.03385,-0.16325,0.03446,0.03693" }, EPSG_4218: { towgs84: "304.5,306.5,-318.1" }, EPSG_4242: { towgs84: "-33.722,153.789,94.959,-8.581,-4.478,4.54,8.95" }, EPSG_4216: { towgs84: "-292.295,248.758,429.447,4.9971,2.99,6.6906,1.0289" }, ESRI_104105: { towgs84: "631.392,-66.551,481.442,1.09,-4.445,-4.487,-4.43" }, ESRI_104129: { towgs84: "0,0,0" }, EPSG_4673: { towgs84: "174.05,-25.49,112.57" }, EPSG_4202: { towgs84: "-124,-60,154" }, EPSG_4203: { towgs84: "-117.763,-51.51,139.061,0.292,0.443,0.277,-0.191" }, EPSG_3819: { towgs84: "595.48,121.69,515.35,4.115,-2.9383,0.853,-3.408" }, EPSG_8694: { towgs84: "-93.799,-132.737,-219.073,-1.844,0.648,-6.37,-0.169" }, EPSG_4145: { towgs84: "275.57,676.78,229.6" }, EPSG_4283: { towgs84: "61.55,-10.87,-40.19,39.4924,32.7221,32.8979,-9.994" }, EPSG_4317: { towgs84: "2.3287,-147.0425,-92.0802,-0.3092483,0.32482185,0.49729934,5.68906266" }, EPSG_4272: { towgs84: "59.47,-5.04,187.44,0.47,-0.1,1.024,-4.5993" }, EPSG_4248: { towgs84: "-307.7,265.3,-363.5" }, EPSG_5561: { towgs84: "24,-121,-76" }, EPSG_5233: { towgs84: "-0.293,766.95,87.713,0.195704,1.695068,3.473016,-0.039338" }, ESRI_104130: { towgs84: "-86,-98,-119" }, ESRI_104102: { towgs84: "682,-203,480" }, ESRI_37207: { towgs84: "7,-10,-26" }, EPSG_4675: { towgs84: "59.935,118.4,-10.871" }, ESRI_104109: { towgs84: "-89.121,-348.182,260.871" }, ESRI_104112: { towgs84: "-185.583,-230.096,281.361" }, ESRI_104113: { towgs84: "25.1,-275.6,222.6" }, IGNF_WGS72G: { towgs84: "0,12,6" }, IGNF_NTFG: { towgs84: "-168,-60,320" }, IGNF_EFATE57G: { towgs84: "-127,-769,472" }, IGNF_PGP50G: { towgs84: "324.8,153.6,172.1" }, IGNF_REUN47G: { towgs84: "94,-948,-1262" }, IGNF_CSG67G: { towgs84: "-186,230,110" }, IGNF_GUAD48G: { towgs84: "-467,-16,-300" }, IGNF_TAHI51G: { towgs84: "162,117,154" }, IGNF_TAHAAG: { towgs84: "65,342,77" }, IGNF_NUKU72G: { towgs84: "84,274,65" }, IGNF_PETRELS72G: { towgs84: "365,194,166" }, IGNF_WALL78G: { towgs84: "253,-133,-127" }, IGNF_MAYO50G: { towgs84: "-382,-59,-262" }, IGNF_TANNAG: { towgs84: "-139,-967,436" }, IGNF_IGN72G: { towgs84: "-13,-348,292" }, IGNF_ATIGG: { towgs84: "1118,23,66" }, IGNF_FANGA84G: { towgs84: "150.57,158.33,118.32" }, IGNF_RUSAT84G: { towgs84: "202.13,174.6,-15.74" }, IGNF_KAUE70G: { towgs84: "126.74,300.1,-75.49" }, IGNF_MOP90G: { towgs84: "-10.8,-1.8,12.77" }, IGNF_MHPF67G: { towgs84: "338.08,212.58,-296.17" }, IGNF_TAHI79G: { towgs84: "160.61,116.05,153.69" }, IGNF_ANAA92G: { towgs84: "1.5,3.84,4.81" }, IGNF_MARQUI72G: { towgs84: "330.91,-13.92,58.56" }, IGNF_APAT86G: { towgs84: "143.6,197.82,74.05" }, IGNF_TUBU69G: { towgs84: "237.17,171.61,-77.84" }, IGNF_STPM50G: { towgs84: "11.363,424.148,373.13" }, EPSG_4150: { towgs84: "674.374,15.056,405.346" }, EPSG_4754: { towgs84: "-208.4058,-109.8777,-2.5764" }, ESRI_104101: { towgs84: "374,150,588" }, EPSG_4693: { towgs84: "0,-0.15,0.68" }, EPSG_6207: { towgs84: "293.17,726.18,245.36" }, EPSG_4153: { towgs84: "-133.63,-157.5,-158.62" }, EPSG_4132: { towgs84: "-241.54,-163.64,396.06" }, EPSG_4221: { towgs84: "-154.5,150.7,100.4" }, EPSG_4266: { towgs84: "-80.7,-132.5,41.1" }, EPSG_4193: { towgs84: "-70.9,-151.8,-41.4" }, EPSG_5340: { towgs84: "-0.41,0.46,-0.35" }, EPSG_4246: { towgs84: "-294.7,-200.1,525.5" }, EPSG_4318: { towgs84: "-3.2,-5.7,2.8" }, EPSG_4121: { towgs84: "-199.87,74.79,246.62" }, EPSG_4223: { towgs84: "-260.1,5.5,432.2" }, EPSG_4158: { towgs84: "-0.465,372.095,171.736" }, EPSG_4285: { towgs84: "-128.16,-282.42,21.93" }, EPSG_4613: { towgs84: "-404.78,685.68,45.47" }, EPSG_4607: { towgs84: "195.671,332.517,274.607" }, EPSG_4475: { towgs84: "-381.788,-57.501,-256.673" }, EPSG_4208: { towgs84: "-157.84,308.54,-146.6" }, EPSG_4743: { towgs84: "70.995,-335.916,262.898" }, EPSG_4710: { towgs84: "-323.65,551.39,-491.22" }, EPSG_7881: { towgs84: "-0.077,0.079,0.086" }, EPSG_4682: { towgs84: "283.729,735.942,261.143" }, EPSG_4739: { towgs84: "-156,-271,-189" }, EPSG_4679: { towgs84: "-80.01,253.26,291.19" }, EPSG_4750: { towgs84: "-56.263,16.136,-22.856" }, EPSG_4644: { towgs84: "-10.18,-350.43,291.37" }, EPSG_4695: { towgs84: "-103.746,-9.614,-255.95" }, EPSG_4292: { towgs84: "-355,21,72" }, EPSG_4302: { towgs84: "-61.702,284.488,472.052" }, EPSG_4143: { towgs84: "-124.76,53,466.79" }, EPSG_4606: { towgs84: "-153,153,307" }, EPSG_4699: { towgs84: "-770.1,158.4,-498.2" }, EPSG_4247: { towgs84: "-273.5,110.6,-357.9" }, EPSG_4160: { towgs84: "8.88,184.86,106.69" }, EPSG_4161: { towgs84: "-233.43,6.65,173.64" }, EPSG_9251: { towgs84: "-9.5,122.9,138.2" }, EPSG_9253: { towgs84: "-78.1,101.6,133.3" }, EPSG_4297: { towgs84: "-198.383,-240.517,-107.909" }, EPSG_4269: { towgs84: "0,0,0" }, EPSG_4301: { towgs84: "-147,506,687" }, EPSG_4618: { towgs84: "-59,-11,-52" }, EPSG_4612: { towgs84: "0,0,0" }, EPSG_4678: { towgs84: "44.585,-131.212,-39.544" }, EPSG_4250: { towgs84: "-130,29,364" }, EPSG_4144: { towgs84: "214,804,268" }, EPSG_4147: { towgs84: "-17.51,-108.32,-62.39" }, EPSG_4259: { towgs84: "-254.1,-5.36,-100.29" }, EPSG_4164: { towgs84: "-76,-138,67" }, EPSG_4211: { towgs84: "-378.873,676.002,-46.255" }, EPSG_4182: { towgs84: "-422.651,-172.995,84.02" }, EPSG_4224: { towgs84: "-143.87,243.37,-33.52" }, EPSG_4225: { towgs84: "-205.57,168.77,-4.12" }, EPSG_5527: { towgs84: "-67.35,3.88,-38.22" }, EPSG_4752: { towgs84: "98,390,-22" }, EPSG_4310: { towgs84: "-30,190,89" }, EPSG_9248: { towgs84: "-192.26,65.72,132.08" }, EPSG_4680: { towgs84: "124.5,-63.5,-281" }, EPSG_4701: { towgs84: "-79.9,-158,-168.9" }, EPSG_4706: { towgs84: "-146.21,112.63,4.05" }, EPSG_4805: { towgs84: "682,-203,480" }, EPSG_4201: { towgs84: "-165,-11,206" }, EPSG_4210: { towgs84: "-157,-2,-299" }, EPSG_4183: { towgs84: "-104,167,-38" }, EPSG_4139: { towgs84: "11,72,-101" }, EPSG_4668: { towgs84: "-86,-98,-119" }, EPSG_4717: { towgs84: "-2,151,181" }, EPSG_4732: { towgs84: "102,52,-38" }, EPSG_4280: { towgs84: "-377,681,-50" }, EPSG_4209: { towgs84: "-138,-105,-289" }, EPSG_4261: { towgs84: "31,146,47" }, EPSG_4658: { towgs84: "-73,46,-86" }, EPSG_4721: { towgs84: "265.025,384.929,-194.046" }, EPSG_4222: { towgs84: "-136,-108,-292" }, EPSG_4601: { towgs84: "-255,-15,71" }, EPSG_4602: { towgs84: "725,685,536" }, EPSG_4603: { towgs84: "72,213.7,93" }, EPSG_4605: { towgs84: "9,183,236" }, EPSG_4621: { towgs84: "137,248,-430" }, EPSG_4657: { towgs84: "-28,199,5" }, EPSG_4316: { towgs84: "103.25,-100.4,-307.19" }, EPSG_4642: { towgs84: "-13,-348,292" }, EPSG_4698: { towgs84: "145,-187,103" }, EPSG_4192: { towgs84: "-206.1,-174.7,-87.7" }, EPSG_4311: { towgs84: "-265,120,-358" }, EPSG_4135: { towgs84: "58,-283,-182" }, ESRI_104138: { towgs84: "198,-226,-347" }, EPSG_4245: { towgs84: "-11,851,5" }, EPSG_4142: { towgs84: "-125,53,467" }, EPSG_4213: { towgs84: "-106,-87,188" }, EPSG_4253: { towgs84: "-133,-77,-51" }, EPSG_4129: { towgs84: "-132,-110,-335" }, EPSG_4713: { towgs84: "-77,-128,142" }, EPSG_4239: { towgs84: "217,823,299" }, EPSG_4146: { towgs84: "295,736,257" }, EPSG_4155: { towgs84: "-83,37,124" }, EPSG_4165: { towgs84: "-173,253,27" }, EPSG_4672: { towgs84: "175,-38,113" }, EPSG_4236: { towgs84: "-637,-549,-203" }, EPSG_4251: { towgs84: "-90,40,88" }, EPSG_4271: { towgs84: "-2,374,172" }, EPSG_4175: { towgs84: "-88,4,101" }, EPSG_4716: { towgs84: "298,-304,-375" }, EPSG_4315: { towgs84: "-23,259,-9" }, EPSG_4744: { towgs84: "-242.2,-144.9,370.3" }, EPSG_4244: { towgs84: "-97,787,86" }, EPSG_4293: { towgs84: "616,97,-251" }, EPSG_4714: { towgs84: "-127,-769,472" }, EPSG_4736: { towgs84: "260,12,-147" }, EPSG_6883: { towgs84: "-235,-110,393" }, EPSG_6894: { towgs84: "-63,176,185" }, EPSG_4205: { towgs84: "-43,-163,45" }, EPSG_4256: { towgs84: "41,-220,-134" }, EPSG_4262: { towgs84: "639,405,60" }, EPSG_4604: { towgs84: "174,359,365" }, EPSG_4169: { towgs84: "-115,118,426" }, EPSG_4620: { towgs84: "-106,-129,165" }, EPSG_4184: { towgs84: "-203,141,53" }, EPSG_4616: { towgs84: "-289,-124,60" }, EPSG_9403: { towgs84: "-307,-92,127" }, EPSG_4684: { towgs84: "-133,-321,50" }, EPSG_4708: { towgs84: "-491,-22,435" }, EPSG_4707: { towgs84: "114,-116,-333" }, EPSG_4709: { towgs84: "145,75,-272" }, EPSG_4712: { towgs84: "-205,107,53" }, EPSG_4711: { towgs84: "124,-234,-25" }, EPSG_4718: { towgs84: "230,-199,-752" }, EPSG_4719: { towgs84: "211,147,111" }, EPSG_4724: { towgs84: "208,-435,-229" }, EPSG_4725: { towgs84: "189,-79,-202" }, EPSG_4735: { towgs84: "647,1777,-1124" }, EPSG_4722: { towgs84: "-794,119,-298" }, EPSG_4728: { towgs84: "-307,-92,127" }, EPSG_4734: { towgs84: "-632,438,-609" }, EPSG_4727: { towgs84: "912,-58,1227" }, EPSG_4729: { towgs84: "185,165,42" }, EPSG_4730: { towgs84: "170,42,84" }, EPSG_4733: { towgs84: "276,-57,149" }, ESRI_37218: { towgs84: "230,-199,-752" }, ESRI_37240: { towgs84: "-7,215,225" }, ESRI_37221: { towgs84: "252,-209,-751" }, ESRI_4305: { towgs84: "-123,-206,219" }, ESRI_104139: { towgs84: "-73,-247,227" }, EPSG_4748: { towgs84: "51,391,-36" }, EPSG_4219: { towgs84: "-384,664,-48" }, EPSG_4255: { towgs84: "-333,-222,114" }, EPSG_4257: { towgs84: "-587.8,519.75,145.76" }, EPSG_4646: { towgs84: "-963,510,-359" }, EPSG_6881: { towgs84: "-24,-203,268" }, EPSG_6882: { towgs84: "-183,-15,273" }, EPSG_4715: { towgs84: "-104,-129,239" }, IGNF_RGF93GDD: { towgs84: "0,0,0" }, IGNF_RGM04GDD: { towgs84: "0,0,0" }, IGNF_RGSPM06GDD: { towgs84: "0,0,0" }, IGNF_RGTAAF07GDD: { towgs84: "0,0,0" }, IGNF_RGFG95GDD: { towgs84: "0,0,0" }, IGNF_RGNCG: { towgs84: "0,0,0" }, IGNF_RGPFGDD: { towgs84: "0,0,0" }, IGNF_ETRS89G: { towgs84: "0,0,0" }, IGNF_RGR92GDD: { towgs84: "0,0,0" }, EPSG_4173: { towgs84: "0,0,0" }, EPSG_4180: { towgs84: "0,0,0" }, EPSG_4619: { towgs84: "0,0,0" }, EPSG_4667: { towgs84: "0,0,0" }, EPSG_4075: { towgs84: "0,0,0" }, EPSG_6706: { towgs84: "0,0,0" }, EPSG_7798: { towgs84: "0,0,0" }, EPSG_4661: { towgs84: "0,0,0" }, EPSG_4669: { towgs84: "0,0,0" }, EPSG_8685: { towgs84: "0,0,0" }, EPSG_4151: { towgs84: "0,0,0" }, EPSG_9702: { towgs84: "0,0,0" }, EPSG_4758: { towgs84: "0,0,0" }, EPSG_4761: { towgs84: "0,0,0" }, EPSG_4765: { towgs84: "0,0,0" }, EPSG_8997: { towgs84: "0,0,0" }, EPSG_4023: { towgs84: "0,0,0" }, EPSG_4670: { towgs84: "0,0,0" }, EPSG_4694: { towgs84: "0,0,0" }, EPSG_4148: { towgs84: "0,0,0" }, EPSG_4163: { towgs84: "0,0,0" }, EPSG_4167: { towgs84: "0,0,0" }, EPSG_4189: { towgs84: "0,0,0" }, EPSG_4190: { towgs84: "0,0,0" }, EPSG_4176: { towgs84: "0,0,0" }, EPSG_4659: { towgs84: "0,0,0" }, EPSG_3824: { towgs84: "0,0,0" }, EPSG_3889: { towgs84: "0,0,0" }, EPSG_4046: { towgs84: "0,0,0" }, EPSG_4081: { towgs84: "0,0,0" }, EPSG_4558: { towgs84: "0,0,0" }, EPSG_4483: { towgs84: "0,0,0" }, EPSG_5013: { towgs84: "0,0,0" }, EPSG_5264: { towgs84: "0,0,0" }, EPSG_5324: { towgs84: "0,0,0" }, EPSG_5354: { towgs84: "0,0,0" }, EPSG_5371: { towgs84: "0,0,0" }, EPSG_5373: { towgs84: "0,0,0" }, EPSG_5381: { towgs84: "0,0,0" }, EPSG_5393: { towgs84: "0,0,0" }, EPSG_5489: { towgs84: "0,0,0" }, EPSG_5593: { towgs84: "0,0,0" }, EPSG_6135: { towgs84: "0,0,0" }, EPSG_6365: { towgs84: "0,0,0" }, EPSG_5246: { towgs84: "0,0,0" }, EPSG_7886: { towgs84: "0,0,0" }, EPSG_8431: { towgs84: "0,0,0" }, EPSG_8427: { towgs84: "0,0,0" }, EPSG_8699: { towgs84: "0,0,0" }, EPSG_8818: { towgs84: "0,0,0" }, EPSG_4757: { towgs84: "0,0,0" }, EPSG_9140: { towgs84: "0,0,0" }, EPSG_8086: { towgs84: "0,0,0" }, EPSG_4686: { towgs84: "0,0,0" }, EPSG_4737: { towgs84: "0,0,0" }, EPSG_4702: { towgs84: "0,0,0" }, EPSG_4747: { towgs84: "0,0,0" }, EPSG_4749: { towgs84: "0,0,0" }, EPSG_4674: { towgs84: "0,0,0" }, EPSG_4755: { towgs84: "0,0,0" }, EPSG_4759: { towgs84: "0,0,0" }, EPSG_4762: { towgs84: "0,0,0" }, EPSG_4763: { towgs84: "0,0,0" }, EPSG_4764: { towgs84: "0,0,0" }, EPSG_4166: { towgs84: "0,0,0" }, EPSG_4170: { towgs84: "0,0,0" }, EPSG_5546: { towgs84: "0,0,0" }, EPSG_7844: { towgs84: "0,0,0" }, EPSG_4818: { towgs84: "589,76,480" } };
  for (var Vt in $t) {
    var Lt = $t[Vt];
    Lt.datumName && ($t[Lt.datumName] = Lt);
  }
  function Bt(t2, n2, o2, h2, l2, u2, d2) {
    var m2 = {};
    return m2.datum_type = void 0 === t2 || "none" === t2 ? a : r, n2 && (m2.datum_params = n2.map(parseFloat), 0 === m2.datum_params[0] && 0 === m2.datum_params[1] && 0 === m2.datum_params[2] || (m2.datum_type = e), m2.datum_params.length > 3 && (0 === m2.datum_params[3] && 0 === m2.datum_params[4] && 0 === m2.datum_params[5] && 0 === m2.datum_params[6] || (m2.datum_type = i, m2.datum_params[3] *= c, m2.datum_params[4] *= c, m2.datum_params[5] *= c, m2.datum_params[6] = m2.datum_params[6] / 1e6 + 1))), d2 && (m2.datum_type = s, m2.grids = d2), m2.a = o2, m2.b = h2, m2.es = l2, m2.ep2 = u2, m2;
  }
  var qt = {};
  function jt(t2, e2, i2) {
    return e2 instanceof ArrayBuffer ? Ft(t2, e2, i2) : { ready: kt(t2, e2) };
  }
  function Ft(t2, e2, i2) {
    var s2 = true;
    void 0 !== i2 && false === i2.includeErrorFields && (s2 = false);
    var r2 = new DataView(e2), a2 = Qt(r2), n2 = Ht(r2, a2), o2 = { header: n2, subgrids: Yt(r2, n2, a2, s2) };
    return qt[t2] = o2, o2;
  }
  async function kt(t2, e2) {
    for (var i2 = [], s2 = await e2.getImageCount(), r2 = s2 - 1; r2 >= 0; r2--) {
      var a2 = await e2.getImage(r2), n2 = await a2.readRasters(), o2 = [a2.getWidth(), a2.getHeight()], h2 = a2.getBoundingBox().map(Wt), c2 = [a2.fileDirectory.ModelPixelScale[0], a2.fileDirectory.ModelPixelScale[1]].map(Wt), l2 = h2[0] + (o2[0] - 1) * c2[0], u2 = h2[3] - (o2[1] - 1) * c2[1], d2 = n2[0], m2 = n2[1], _2 = [];
      for (let t3 = o2[1] - 1; t3 >= 0; t3--)
        for (let e3 = o2[0] - 1; e3 >= 0; e3--) {
          var f2 = t3 * o2[0] + e3;
          _2.push([-Xt(m2[f2]), Xt(d2[f2])]);
        }
      i2.push({ del: c2, lim: o2, ll: [-l2, u2], cvs: _2 });
    }
    var p2 = { header: { nSubgrids: s2 }, subgrids: i2 };
    return qt[t2] = p2, p2;
  }
  function Dt(t2) {
    return void 0 === t2 ? null : t2.split(",").map(Ut);
  }
  function Ut(t2) {
    if (0 === t2.length)
      return null;
    var e2 = "@" === t2[0];
    return e2 && (t2 = t2.slice(1)), "null" === t2 ? { name: "null", mandatory: !e2, grid: null, isNull: true } : { name: t2, mandatory: !e2, grid: qt[t2] || null, isNull: false };
  }
  function Wt(t2) {
    return t2 * Math.PI / 180;
  }
  function Xt(t2) {
    return t2 / 3600 * Math.PI / 180;
  }
  function Qt(t2) {
    var e2 = t2.getInt32(8, false);
    return 11 !== e2 && (11 !== (e2 = t2.getInt32(8, true)) && console.warn("Failed to detect nadgrid endian-ness, defaulting to little-endian"), true);
  }
  function Ht(t2, e2) {
    return { nFields: t2.getInt32(8, e2), nSubgridFields: t2.getInt32(24, e2), nSubgrids: t2.getInt32(40, e2), shiftType: Zt(t2, 56, 64).trim(), fromSemiMajorAxis: t2.getFloat64(120, e2), fromSemiMinorAxis: t2.getFloat64(136, e2), toSemiMajorAxis: t2.getFloat64(152, e2), toSemiMinorAxis: t2.getFloat64(168, e2) };
  }
  function Zt(t2, e2, i2) {
    return String.fromCharCode.apply(null, new Uint8Array(t2.buffer.slice(e2, i2)));
  }
  function Yt(t2, e2, i2, s2) {
    for (var r2 = 176, a2 = [], n2 = 0; n2 < e2.nSubgrids; n2++) {
      var o2 = Kt(t2, r2, i2), h2 = te(t2, r2, o2, i2, s2), c2 = Math.round(1 + (o2.upperLongitude - o2.lowerLongitude) / o2.longitudeInterval), l2 = Math.round(1 + (o2.upperLatitude - o2.lowerLatitude) / o2.latitudeInterval);
      a2.push({ ll: [Xt(o2.lowerLongitude), Xt(o2.lowerLatitude)], del: [Xt(o2.longitudeInterval), Xt(o2.latitudeInterval)], lim: [c2, l2], count: o2.gridNodeCount, cvs: Jt(h2) });
      var u2 = 16;
      false === s2 && (u2 = 8), r2 += 176 + o2.gridNodeCount * u2;
    }
    return a2;
  }
  function Jt(t2) {
    return t2.map(function(t3) {
      return [Xt(t3.longitudeShift), Xt(t3.latitudeShift)];
    });
  }
  function Kt(t2, e2, i2) {
    return { name: Zt(t2, e2 + 8, e2 + 16).trim(), parent: Zt(t2, e2 + 24, e2 + 24 + 8).trim(), lowerLatitude: t2.getFloat64(e2 + 72, i2), upperLatitude: t2.getFloat64(e2 + 88, i2), lowerLongitude: t2.getFloat64(e2 + 104, i2), upperLongitude: t2.getFloat64(e2 + 120, i2), latitudeInterval: t2.getFloat64(e2 + 136, i2), longitudeInterval: t2.getFloat64(e2 + 152, i2), gridNodeCount: t2.getInt32(e2 + 168, i2) };
  }
  function te(t2, e2, i2, s2, r2) {
    var a2 = e2 + 176, n2 = 16;
    false === r2 && (n2 = 8);
    for (var o2 = [], h2 = 0; h2 < i2.gridNodeCount; h2++) {
      var c2 = { latitudeShift: t2.getFloat32(a2 + h2 * n2, s2), longitudeShift: t2.getFloat32(a2 + h2 * n2 + 4, s2) };
      false !== r2 && (c2.latitudeAccuracy = t2.getFloat32(a2 + h2 * n2 + 8, s2), c2.longitudeAccuracy = t2.getFloat32(a2 + h2 * n2 + 12, s2)), o2.push(c2);
    }
    return o2;
  }
  function ee(t2, e2) {
    if (!(this instanceof ee))
      return new ee(t2);
    this.forward = null, this.inverse = null, this.init = null, this.name, this.names = null, this.title, e2 = e2 || function(t3) {
      if (t3)
        throw t3;
    };
    var i2 = dt(t2);
    if ("object" == typeof i2) {
      var s2 = ee.projections.get(i2.projName);
      if (s2) {
        if (i2.datumCode && "none" !== i2.datumCode) {
          var r2 = S($t, i2.datumCode);
          r2 && (i2.datum_params = i2.datum_params || (r2.towgs84 ? r2.towgs84.split(",") : null), i2.ellps = r2.ellipse, i2.datumName = r2.datumName ? r2.datumName : i2.datumCode);
        }
        i2.k0 = i2.k0 || 1, i2.axis = i2.axis || "enu", i2.ellps = i2.ellps || "wgs84", i2.lat1 = i2.lat1 || i2.lat0;
        var a2 = Rt(i2.a, i2.b, i2.rf, i2.ellps, i2.sphere), n2 = It(a2.a, a2.b, a2.rf, i2.R_A), o2 = Dt(i2.nadgrids), h2 = i2.datum || Bt(i2.datumCode, i2.datum_params, a2.a, a2.b, n2.es, n2.ep2, o2);
        mt(this, i2), mt(this, s2), this.a = a2.a, this.b = a2.b, this.rf = a2.rf, this.sphere = a2.sphere, this.es = n2.es, this.e = n2.e, this.ep2 = n2.ep2, this.datum = h2, "init" in this && "function" == typeof this.init && this.init(), e2(null, this);
      } else
        e2("Could not get projection name from: " + t2);
    } else
      e2("Could not parse to valid json: " + t2);
  }
  function ie(t2, s2) {
    return t2.datum_type === s2.datum_type && !(t2.a !== s2.a || Math.abs(t2.es - s2.es) > 5e-11) && (t2.datum_type === e ? t2.datum_params[0] === s2.datum_params[0] && t2.datum_params[1] === s2.datum_params[1] && t2.datum_params[2] === s2.datum_params[2] : t2.datum_type !== i || t2.datum_params[0] === s2.datum_params[0] && t2.datum_params[1] === s2.datum_params[1] && t2.datum_params[2] === s2.datum_params[2] && t2.datum_params[3] === s2.datum_params[3] && t2.datum_params[4] === s2.datum_params[4] && t2.datum_params[5] === s2.datum_params[5] && t2.datum_params[6] === s2.datum_params[6]);
  }
  function se(t2, e2, i2) {
    var s2, r2, a2, n2, o2 = t2.x, h2 = t2.y, c2 = t2.z ? t2.z : 0;
    if (h2 < -l && h2 > -1.001 * l)
      h2 = -l;
    else if (h2 > l && h2 < 1.001 * l)
      h2 = l;
    else {
      if (h2 < -l)
        return { x: -1 / 0, y: -1 / 0, z: t2.z };
      if (h2 > l)
        return { x: 1 / 0, y: 1 / 0, z: t2.z };
    }
    return o2 > Math.PI && (o2 -= 2 * Math.PI), r2 = Math.sin(h2), n2 = Math.cos(h2), a2 = r2 * r2, { x: ((s2 = i2 / Math.sqrt(1 - e2 * a2)) + c2) * n2 * Math.cos(o2), y: (s2 + c2) * n2 * Math.sin(o2), z: (s2 * (1 - e2) + c2) * r2 };
  }
  function re(t2, e2, i2, s2) {
    var r2, a2, n2, o2, h2, c2, l2, u2, d2, m2, _2, f2, p2, x2, y2, M2 = 1e-12, g2 = M2 * M2, w2 = 30, P2 = t2.x, S2 = t2.y, C2 = t2.z ? t2.z : 0;
    if (r2 = Math.sqrt(P2 * P2 + S2 * S2), a2 = Math.sqrt(P2 * P2 + S2 * S2 + C2 * C2), r2 / i2 < M2) {
      if (x2 = 0, a2 / i2 < M2)
        return y2 = -s2, { x: t2.x, y: t2.y, z: t2.z };
    } else
      x2 = Math.atan2(S2, P2);
    n2 = C2 / a2, u2 = (o2 = r2 / a2) * (1 - e2) * (h2 = 1 / Math.sqrt(1 - e2 * (2 - e2) * o2 * o2)), d2 = n2 * h2, p2 = 0;
    do {
      p2++, c2 = e2 * (l2 = i2 / Math.sqrt(1 - e2 * d2 * d2)) / (l2 + (y2 = r2 * u2 + C2 * d2 - l2 * (1 - e2 * d2 * d2))), f2 = (_2 = n2 * (h2 = 1 / Math.sqrt(1 - c2 * (2 - c2) * o2 * o2))) * u2 - (m2 = o2 * (1 - c2) * h2) * d2, u2 = m2, d2 = _2;
    } while (f2 * f2 > g2 && p2 < w2);
    return { x: x2, y: Math.atan(_2 / Math.abs(m2)), z: y2 };
  }
  function ae(t2, s2, r2) {
    if (s2 === e)
      return { x: t2.x + r2[0], y: t2.y + r2[1], z: t2.z + r2[2] };
    if (s2 === i) {
      var a2 = r2[0], n2 = r2[1], o2 = r2[2], h2 = r2[3], c2 = r2[4], l2 = r2[5], u2 = r2[6];
      return { x: u2 * (t2.x - l2 * t2.y + c2 * t2.z) + a2, y: u2 * (l2 * t2.x + t2.y - h2 * t2.z) + n2, z: u2 * (-c2 * t2.x + h2 * t2.y + t2.z) + o2 };
    }
  }
  function ne(t2, s2, r2) {
    if (s2 === e)
      return { x: t2.x - r2[0], y: t2.y - r2[1], z: t2.z - r2[2] };
    if (s2 === i) {
      var a2 = r2[0], n2 = r2[1], o2 = r2[2], h2 = r2[3], c2 = r2[4], l2 = r2[5], u2 = r2[6], d2 = (t2.x - a2) / u2, m2 = (t2.y - n2) / u2, _2 = (t2.z - o2) / u2;
      return { x: d2 + l2 * m2 - c2 * _2, y: -l2 * d2 + m2 + h2 * _2, z: c2 * d2 - h2 * m2 + _2 };
    }
  }
  function oe(t2) {
    return t2 === e || t2 === i;
  }
  function he(t2, e2, i2) {
    if (ie(t2, e2))
      return i2;
    if (t2.datum_type === a || e2.datum_type === a)
      return i2;
    var r2 = t2.a, c2 = t2.es;
    if (t2.datum_type === s) {
      if (0 !== ce(t2, false, i2))
        return;
      r2 = n, c2 = h;
    }
    var l2 = e2.a, u2 = e2.b, d2 = e2.es;
    return e2.datum_type === s && (l2 = n, u2 = o, d2 = h), c2 !== d2 || r2 !== l2 || oe(t2.datum_type) || oe(e2.datum_type) ? (i2 = se(i2, c2, r2), oe(t2.datum_type) && (i2 = ae(i2, t2.datum_type, t2.datum_params)), oe(e2.datum_type) && (i2 = ne(i2, e2.datum_type, e2.datum_params)), i2 = re(i2, d2, l2, u2), e2.datum_type !== s || 0 === ce(e2, true, i2) ? i2 : void 0) : i2;
  }
  function ce(t2, e2, i2) {
    if (null === t2.grids || 0 === t2.grids.length)
      return console.log("Grid shift grids not found"), -1;
    var s2 = { x: -i2.x, y: i2.y }, r2 = { x: Number.NaN, y: Number.NaN }, a2 = [];
    t:
      for (var n2 = 0; n2 < t2.grids.length; n2++) {
        var o2 = t2.grids[n2];
        if (a2.push(o2.name), o2.isNull) {
          r2 = s2;
          break;
        }
        if (null !== o2.grid)
          for (var h2 = o2.grid.subgrids, c2 = 0, l2 = h2.length; c2 < l2; c2++) {
            var u2 = h2[c2], d2 = (Math.abs(u2.del[1]) + Math.abs(u2.del[0])) / 1e4, m2 = u2.ll[0] - d2, _2 = u2.ll[1] - d2, f2 = u2.ll[0] + (u2.lim[0] - 1) * u2.del[0] + d2, x2 = u2.ll[1] + (u2.lim[1] - 1) * u2.del[1] + d2;
            if (!(_2 > s2.y || m2 > s2.x || x2 < s2.y || f2 < s2.x || (r2 = le(s2, e2, u2), isNaN(r2.x))))
              break t;
          }
        else if (o2.mandatory)
          return console.log("Unable to find mandatory grid '" + o2.name + "'"), -1;
      }
    return isNaN(r2.x) ? (console.log("Failed to find a grid shift table for location '" + -s2.x * p + " " + s2.y * p + " tried: '" + a2 + "'"), -1) : (i2.x = -r2.x, i2.y = r2.y, 0);
  }
  function le(t2, e2, i2) {
    var s2 = { x: Number.NaN, y: Number.NaN };
    if (isNaN(t2.x))
      return s2;
    var r2 = { x: t2.x, y: t2.y };
    r2.x -= i2.ll[0], r2.y -= i2.ll[1], r2.x = pt(r2.x - Math.PI) + Math.PI;
    var a2 = ue(r2, i2);
    if (e2) {
      if (isNaN(a2.x))
        return s2;
      a2.x = r2.x - a2.x, a2.y = r2.y - a2.y;
      var n2, o2, h2 = 9, c2 = 1e-12;
      do {
        if (o2 = ue(a2, i2), isNaN(o2.x)) {
          console.log("Inverse grid shift iteration failed, presumably at grid edge.  Using first approximation.");
          break;
        }
        n2 = { x: r2.x - (o2.x + a2.x), y: r2.y - (o2.y + a2.y) }, a2.x += n2.x, a2.y += n2.y;
      } while (h2-- && Math.abs(n2.x) > c2 && Math.abs(n2.y) > c2);
      if (h2 < 0)
        return console.log("Inverse grid shift iterator failed to converge."), s2;
      s2.x = pt(a2.x + i2.ll[0]), s2.y = a2.y + i2.ll[1];
    } else
      isNaN(a2.x) || (s2.x = t2.x + a2.x, s2.y = t2.y + a2.y);
    return s2;
  }
  function ue(t2, e2) {
    var i2, s2 = { x: t2.x / e2.del[0], y: t2.y / e2.del[1] }, r2 = { x: Math.floor(s2.x), y: Math.floor(s2.y) }, a2 = { x: s2.x - 1 * r2.x, y: s2.y - 1 * r2.y }, n2 = { x: Number.NaN, y: Number.NaN };
    if (r2.x < 0 || r2.x >= e2.lim[0])
      return n2;
    if (r2.y < 0 || r2.y >= e2.lim[1])
      return n2;
    i2 = r2.y * e2.lim[0] + r2.x;
    var o2 = { x: e2.cvs[i2][0], y: e2.cvs[i2][1] };
    i2++;
    var h2 = { x: e2.cvs[i2][0], y: e2.cvs[i2][1] };
    i2 += e2.lim[0];
    var c2 = { x: e2.cvs[i2][0], y: e2.cvs[i2][1] };
    i2--;
    var l2 = { x: e2.cvs[i2][0], y: e2.cvs[i2][1] }, u2 = a2.x * a2.y, d2 = a2.x * (1 - a2.y), m2 = (1 - a2.x) * (1 - a2.y), _2 = (1 - a2.x) * a2.y;
    return n2.x = m2 * o2.x + d2 * h2.x + _2 * l2.x + u2 * c2.x, n2.y = m2 * o2.y + d2 * h2.y + _2 * l2.y + u2 * c2.y, n2;
  }
  function de(t2, e2, i2) {
    var s2, r2, a2, n2 = i2.x, o2 = i2.y, h2 = i2.z || 0, c2 = {};
    for (a2 = 0; a2 < 3; a2++)
      if (!e2 || 2 !== a2 || void 0 !== i2.z)
        switch (0 === a2 ? (s2 = n2, r2 = -1 !== "ew".indexOf(t2.axis[a2]) ? "x" : "y") : 1 === a2 ? (s2 = o2, r2 = -1 !== "ns".indexOf(t2.axis[a2]) ? "y" : "x") : (s2 = h2, r2 = "z"), t2.axis[a2]) {
          case "e":
          case "n":
            c2[r2] = s2;
            break;
          case "w":
          case "s":
            c2[r2] = -s2;
            break;
          case "u":
            void 0 !== i2[r2] && (c2.z = s2);
            break;
          case "d":
            void 0 !== i2[r2] && (c2.z = -s2);
            break;
          default:
            return null;
        }
    return c2;
  }
  function me(t2) {
    var e2 = { x: t2[0], y: t2[1] };
    return t2.length > 2 && (e2.z = t2[2]), t2.length > 3 && (e2.m = t2[3]), e2;
  }
  function _e(t2) {
    fe(t2.x), fe(t2.y);
  }
  function fe(t2) {
    if ("function" == typeof Number.isFinite) {
      if (Number.isFinite(t2))
        return;
      throw new TypeError("coordinates must be finite numbers");
    }
    if ("number" != typeof t2 || t2 != t2 || !isFinite(t2))
      throw new TypeError("coordinates must be finite numbers");
  }
  function pe(t2, r2) {
    return (t2.datum.datum_type === e || t2.datum.datum_type === i || t2.datum.datum_type === s) && "WGS84" !== r2.datumCode || (r2.datum.datum_type === e || r2.datum.datum_type === i || r2.datum.datum_type === s) && "WGS84" !== t2.datumCode;
  }
  function xe(t2, e2, i2, s2) {
    var r2, a2 = void 0 !== (i2 = Array.isArray(i2) ? me(i2) : { x: i2.x, y: i2.y, z: i2.z, m: i2.m }).z;
    if (_e(i2), t2.datum && e2.datum && pe(t2, e2) && (i2 = xe(t2, r2 = new ee("WGS84"), i2, s2), t2 = r2), s2 && "enu" !== t2.axis && (i2 = de(t2, false, i2)), "longlat" === t2.projName)
      i2 = { x: i2.x * f, y: i2.y * f, z: i2.z || 0 };
    else if (t2.to_meter && (i2 = { x: i2.x * t2.to_meter, y: i2.y * t2.to_meter, z: i2.z || 0 }), !(i2 = t2.inverse(i2)))
      return;
    if (t2.from_greenwich && (i2.x += t2.from_greenwich), i2 = he(t2.datum, e2.datum, i2))
      return e2.from_greenwich && (i2 = { x: i2.x - e2.from_greenwich, y: i2.y, z: i2.z || 0 }), "longlat" === e2.projName ? i2 = { x: i2.x * p, y: i2.y * p, z: i2.z || 0 } : (i2 = e2.forward(i2), e2.to_meter && (i2 = { x: i2.x / e2.to_meter, y: i2.y / e2.to_meter, z: i2.z || 0 })), s2 && "enu" !== e2.axis ? de(e2, true, i2) : (i2 && !a2 && delete i2.z, i2);
  }
  ee.projections = Ot, ee.projections.start();
  var ye = ee("WGS84");
  function Me(t2, e2, i2, s2) {
    var r2, a2, n2;
    return Array.isArray(i2) ? (r2 = xe(t2, e2, i2, s2) || { x: NaN, y: NaN }, i2.length > 2 ? void 0 !== t2.name && "geocent" === t2.name || void 0 !== e2.name && "geocent" === e2.name ? "number" == typeof r2.z ? [r2.x, r2.y, r2.z].concat(i2.slice(3)) : [r2.x, r2.y, i2[2]].concat(i2.slice(3)) : [r2.x, r2.y].concat(i2.slice(2)) : [r2.x, r2.y]) : (a2 = xe(t2, e2, i2, s2), 2 === (n2 = Object.keys(i2)).length || n2.forEach(function(s3) {
      if (void 0 !== t2.name && "geocent" === t2.name || void 0 !== e2.name && "geocent" === e2.name) {
        if ("x" === s3 || "y" === s3 || "z" === s3)
          return;
      } else if ("x" === s3 || "y" === s3)
        return;
      a2[s3] = i2[s3];
    }), a2);
  }
  function ge(t2) {
    return t2 instanceof ee ? t2 : "object" == typeof t2 && "oProj" in t2 ? t2.oProj : ee(t2);
  }
  function we(t2, e2, i2) {
    var s2, r2, a2, n2 = false;
    return void 0 === e2 ? (r2 = ge(t2), s2 = ye, n2 = true) : (void 0 !== e2.x || Array.isArray(e2)) && (i2 = e2, r2 = ge(t2), s2 = ye, n2 = true), s2 || (s2 = ge(t2)), r2 || (r2 = ge(e2)), i2 ? Me(s2, r2, i2) : (a2 = { forward: function(t3, e3) {
      return Me(s2, r2, t3, e3);
    }, inverse: function(t3, e3) {
      return Me(r2, s2, t3, e3);
    } }, n2 && (a2.oProj = r2), a2);
  }
  var Pe = 6, Se = "AJSAJS", Ce = "AFAFAF", Ee = 65, ve = 73, be = 79, ze = 86, Te = 90, Ae = { forward: Oe, inverse: Ge, toPoint: Ne };
  function Oe(t2, e2) {
    return e2 = e2 || 5, Be($e({ lat: t2[1], lon: t2[0] }), e2);
  }
  function Ge(t2) {
    var e2 = Ve(ke(t2.toUpperCase()));
    return e2.lat && e2.lon ? [e2.lon, e2.lat, e2.lon, e2.lat] : [e2.left, e2.bottom, e2.right, e2.top];
  }
  function Ne(t2) {
    var e2 = Ve(ke(t2.toUpperCase()));
    return e2.lat && e2.lon ? [e2.lon, e2.lat] : [(e2.left + e2.right) / 2, (e2.top + e2.bottom) / 2];
  }
  function Ie(t2) {
    return t2 * (Math.PI / 180);
  }
  function Re(t2) {
    return t2 / Math.PI * 180;
  }
  function $e(t2) {
    var e2, i2, s2, r2, a2, n2, o2, h2 = t2.lat, c2 = t2.lon, l2 = 6378137, u2 = 669438e-8, d2 = 0.9996, m2 = Ie(h2), _2 = Ie(c2);
    o2 = Math.floor((c2 + 180) / 6) + 1, 180 === c2 && (o2 = 60), h2 >= 56 && h2 < 64 && c2 >= 3 && c2 < 12 && (o2 = 32), h2 >= 72 && h2 < 84 && (c2 >= 0 && c2 < 9 ? o2 = 31 : c2 >= 9 && c2 < 21 ? o2 = 33 : c2 >= 21 && c2 < 33 ? o2 = 35 : c2 >= 33 && c2 < 42 && (o2 = 37)), n2 = Ie(6 * (o2 - 1) - 180 + 3), e2 = u2 / (1 - u2), i2 = l2 / Math.sqrt(1 - u2 * Math.sin(m2) * Math.sin(m2)), s2 = Math.tan(m2) * Math.tan(m2), r2 = e2 * Math.cos(m2) * Math.cos(m2);
    var f2 = d2 * i2 * ((a2 = Math.cos(m2) * (_2 - n2)) + (1 - s2 + r2) * a2 * a2 * a2 / 6 + (5 - 18 * s2 + s2 * s2 + 72 * r2 - 58 * e2) * a2 * a2 * a2 * a2 * a2 / 120) + 5e5, p2 = d2 * (l2 * ((1 - u2 / 4 - 3 * u2 * u2 / 64 - 5 * u2 * u2 * u2 / 256) * m2 - (3 * u2 / 8 + 3 * u2 * u2 / 32 + 45 * u2 * u2 * u2 / 1024) * Math.sin(2 * m2) + (15 * u2 * u2 / 256 + 45 * u2 * u2 * u2 / 1024) * Math.sin(4 * m2) - 35 * u2 * u2 * u2 / 3072 * Math.sin(6 * m2)) + i2 * Math.tan(m2) * (a2 * a2 / 2 + (5 - s2 + 9 * r2 + 4 * r2 * r2) * a2 * a2 * a2 * a2 / 24 + (61 - 58 * s2 + s2 * s2 + 600 * r2 - 330 * e2) * a2 * a2 * a2 * a2 * a2 * a2 / 720));
    return h2 < 0 && (p2 += 1e7), { northing: Math.round(p2), easting: Math.round(f2), zoneNumber: o2, zoneLetter: Le(h2) };
  }
  function Ve(t2) {
    var e2 = t2.northing, i2 = t2.easting, s2 = t2.zoneLetter, r2 = t2.zoneNumber;
    if (r2 < 0 || r2 > 60)
      return null;
    var a2, n2, o2, h2, c2, l2, u2, d2, m2, _2 = 0.9996, f2 = 6378137, p2 = 669438e-8, x2 = (1 - Math.sqrt(1 - p2)) / (1 + Math.sqrt(1 - p2)), y2 = i2 - 5e5, M2 = e2;
    s2 < "N" && (M2 -= 1e7), u2 = 6 * (r2 - 1) - 180 + 3, a2 = p2 / (1 - p2), m2 = (d2 = M2 / _2 / (f2 * (1 - p2 / 4 - 3 * p2 * p2 / 64 - 5 * p2 * p2 * p2 / 256))) + (3 * x2 / 2 - 27 * x2 * x2 * x2 / 32) * Math.sin(2 * d2) + (21 * x2 * x2 / 16 - 55 * x2 * x2 * x2 * x2 / 32) * Math.sin(4 * d2) + 151 * x2 * x2 * x2 / 96 * Math.sin(6 * d2), n2 = f2 / Math.sqrt(1 - p2 * Math.sin(m2) * Math.sin(m2)), o2 = Math.tan(m2) * Math.tan(m2), h2 = a2 * Math.cos(m2) * Math.cos(m2), c2 = f2 * (1 - p2) / Math.pow(1 - p2 * Math.sin(m2) * Math.sin(m2), 1.5), l2 = y2 / (n2 * _2);
    var g2 = m2 - n2 * Math.tan(m2) / c2 * (l2 * l2 / 2 - (5 + 3 * o2 + 10 * h2 - 4 * h2 * h2 - 9 * a2) * l2 * l2 * l2 * l2 / 24 + (61 + 90 * o2 + 298 * h2 + 45 * o2 * o2 - 252 * a2 - 3 * h2 * h2) * l2 * l2 * l2 * l2 * l2 * l2 / 720);
    g2 = Re(g2);
    var w2, P2 = (l2 - (1 + 2 * o2 + h2) * l2 * l2 * l2 / 6 + (5 - 2 * h2 + 28 * o2 - 3 * h2 * h2 + 8 * a2 + 24 * o2 * o2) * l2 * l2 * l2 * l2 * l2 / 120) / Math.cos(m2);
    if (P2 = u2 + Re(P2), t2.accuracy) {
      var S2 = Ve({ northing: t2.northing + t2.accuracy, easting: t2.easting + t2.accuracy, zoneLetter: t2.zoneLetter, zoneNumber: t2.zoneNumber });
      w2 = { top: S2.lat, right: S2.lon, bottom: g2, left: P2 };
    } else
      w2 = { lat: g2, lon: P2 };
    return w2;
  }
  function Le(t2) {
    var e2 = "Z";
    return 84 >= t2 && t2 >= 72 ? e2 = "X" : 72 > t2 && t2 >= 64 ? e2 = "W" : 64 > t2 && t2 >= 56 ? e2 = "V" : 56 > t2 && t2 >= 48 ? e2 = "U" : 48 > t2 && t2 >= 40 ? e2 = "T" : 40 > t2 && t2 >= 32 ? e2 = "S" : 32 > t2 && t2 >= 24 ? e2 = "R" : 24 > t2 && t2 >= 16 ? e2 = "Q" : 16 > t2 && t2 >= 8 ? e2 = "P" : 8 > t2 && t2 >= 0 ? e2 = "N" : 0 > t2 && t2 >= -8 ? e2 = "M" : -8 > t2 && t2 >= -16 ? e2 = "L" : -16 > t2 && t2 >= -24 ? e2 = "K" : -24 > t2 && t2 >= -32 ? e2 = "J" : -32 > t2 && t2 >= -40 ? e2 = "H" : -40 > t2 && t2 >= -48 ? e2 = "G" : -48 > t2 && t2 >= -56 ? e2 = "F" : -56 > t2 && t2 >= -64 ? e2 = "E" : -64 > t2 && t2 >= -72 ? e2 = "D" : -72 > t2 && t2 >= -80 && (e2 = "C"), e2;
  }
  function Be(t2, e2) {
    var i2 = "00000" + t2.easting, s2 = "00000" + t2.northing;
    return t2.zoneNumber + t2.zoneLetter + qe(t2.easting, t2.northing, t2.zoneNumber) + i2.substr(i2.length - 5, e2) + s2.substr(s2.length - 5, e2);
  }
  function qe(t2, e2, i2) {
    var s2 = je(i2);
    return Fe(Math.floor(t2 / 1e5), Math.floor(e2 / 1e5) % 20, s2);
  }
  function je(t2) {
    var e2 = t2 % Pe;
    return 0 === e2 && (e2 = Pe), e2;
  }
  function Fe(t2, e2, i2) {
    var s2 = i2 - 1, r2 = Se.charCodeAt(s2), a2 = Ce.charCodeAt(s2), n2 = r2 + t2 - 1, o2 = a2 + e2, h2 = false;
    return n2 > Te && (n2 = n2 - Te + Ee - 1, h2 = true), (n2 === ve || r2 < ve && n2 > ve || (n2 > ve || r2 < ve) && h2) && n2++, (n2 === be || r2 < be && n2 > be || (n2 > be || r2 < be) && h2) && ++n2 === ve && n2++, n2 > Te && (n2 = n2 - Te + Ee - 1), o2 > ze ? (o2 = o2 - ze + Ee - 1, h2 = true) : h2 = false, (o2 === ve || a2 < ve && o2 > ve || (o2 > ve || a2 < ve) && h2) && o2++, (o2 === be || a2 < be && o2 > be || (o2 > be || a2 < be) && h2) && ++o2 === ve && o2++, o2 > ze && (o2 = o2 - ze + Ee - 1), String.fromCharCode(n2) + String.fromCharCode(o2);
  }
  function ke(t2) {
    if (t2 && 0 === t2.length)
      throw "MGRSPoint coverting from nothing";
    for (var e2, i2 = t2.length, s2 = null, r2 = "", a2 = 0; !/[A-Z]/.test(e2 = t2.charAt(a2)); ) {
      if (a2 >= 2)
        throw "MGRSPoint bad conversion from: " + t2;
      r2 += e2, a2++;
    }
    var n2 = parseInt(r2, 10);
    if (0 === a2 || a2 + 3 > i2)
      throw "MGRSPoint bad conversion from: " + t2;
    var o2 = t2.charAt(a2++);
    if (o2 <= "A" || "B" === o2 || "Y" === o2 || o2 >= "Z" || "I" === o2 || "O" === o2)
      throw "MGRSPoint zone letter " + o2 + " not handled: " + t2;
    s2 = t2.substring(a2, a2 += 2);
    for (var h2 = je(n2), c2 = De(s2.charAt(0), h2), l2 = Ue(s2.charAt(1), h2); l2 < We(o2); )
      l2 += 2e6;
    var u2 = i2 - a2;
    if (u2 % 2 != 0)
      throw "MGRSPoint has to have an even number \nof digits after the zone letter and two 100km letters - front \nhalf for easting meters, second half for \nnorthing meters" + t2;
    var d2, m2, _2, f2 = u2 / 2, p2 = 0, x2 = 0;
    return f2 > 0 && (d2 = 1e5 / Math.pow(10, f2), m2 = t2.substring(a2, a2 + f2), p2 = parseFloat(m2) * d2, _2 = t2.substring(a2 + f2), x2 = parseFloat(_2) * d2), { easting: p2 + c2, northing: x2 + l2, zoneLetter: o2, zoneNumber: n2, accuracy: d2 };
  }
  function De(t2, e2) {
    for (var i2 = Se.charCodeAt(e2 - 1), s2 = 1e5, r2 = false; i2 !== t2.charCodeAt(0); ) {
      if (++i2 === ve && i2++, i2 === be && i2++, i2 > Te) {
        if (r2)
          throw "Bad character: " + t2;
        i2 = Ee, r2 = true;
      }
      s2 += 1e5;
    }
    return s2;
  }
  function Ue(t2, e2) {
    if (t2 > "V")
      throw "MGRSPoint given invalid Northing " + t2;
    for (var i2 = Ce.charCodeAt(e2 - 1), s2 = 0, r2 = false; i2 !== t2.charCodeAt(0); ) {
      if (++i2 === ve && i2++, i2 === be && i2++, i2 > ze) {
        if (r2)
          throw "Bad character: " + t2;
        i2 = Ee, r2 = true;
      }
      s2 += 1e5;
    }
    return s2;
  }
  function We(t2) {
    var e2;
    switch (t2) {
      case "C":
        e2 = 11e5;
        break;
      case "D":
        e2 = 2e6;
        break;
      case "E":
        e2 = 28e5;
        break;
      case "F":
        e2 = 37e5;
        break;
      case "G":
        e2 = 46e5;
        break;
      case "H":
        e2 = 55e5;
        break;
      case "J":
        e2 = 64e5;
        break;
      case "K":
        e2 = 73e5;
        break;
      case "L":
        e2 = 82e5;
        break;
      case "M":
        e2 = 91e5;
        break;
      case "N":
        e2 = 0;
        break;
      case "P":
        e2 = 8e5;
        break;
      case "Q":
        e2 = 17e5;
        break;
      case "R":
        e2 = 26e5;
        break;
      case "S":
        e2 = 35e5;
        break;
      case "T":
        e2 = 44e5;
        break;
      case "U":
        e2 = 53e5;
        break;
      case "V":
        e2 = 62e5;
        break;
      case "W":
        e2 = 7e6;
        break;
      case "X":
        e2 = 79e5;
        break;
      default:
        e2 = -1;
    }
    if (e2 >= 0)
      return e2;
    throw "Invalid zone letter: " + t2;
  }
  function Xe(t2, e2, i2) {
    if (!(this instanceof Xe))
      return new Xe(t2, e2, i2);
    if (Array.isArray(t2))
      this.x = t2[0], this.y = t2[1], this.z = t2[2] || 0;
    else if ("object" == typeof t2)
      this.x = t2.x, this.y = t2.y, this.z = t2.z || 0;
    else if ("string" == typeof t2 && void 0 === e2) {
      var s2 = t2.split(",");
      this.x = parseFloat(s2[0]), this.y = parseFloat(s2[1]), this.z = parseFloat(s2[2]) || 0;
    } else
      this.x = t2, this.y = e2, this.z = i2 || 0;
    console.warn("proj4.Point will be removed in version 3, use proj4.toPoint");
  }
  Xe.fromMGRS = function(t2) {
    return new Xe(Ne(t2));
  }, Xe.prototype.toMGRS = function(t2) {
    return Oe([this.x, this.y], t2);
  };
  var Qe = 1, He = 0.25, Ze = 0.046875, Ye = 0.01953125, Je = 0.01068115234375, Ke = 0.75, ti = 0.46875, ei = 0.013020833333333334, ii = 0.007120768229166667, si = 0.3645833333333333, ri = 0.005696614583333333, ai = 0.3076171875;
  function ni(t2) {
    var e2 = [];
    e2[0] = Qe - t2 * (He + t2 * (Ze + t2 * (Ye + t2 * Je))), e2[1] = t2 * (Ke - t2 * (Ze + t2 * (Ye + t2 * Je)));
    var i2 = t2 * t2;
    return e2[2] = i2 * (ti - t2 * (ei + t2 * ii)), i2 *= t2, e2[3] = i2 * (si - t2 * ri), e2[4] = i2 * t2 * ai, e2;
  }
  function oi(t2, e2, i2, s2) {
    return i2 *= e2, e2 *= e2, s2[0] * t2 - i2 * (s2[1] + e2 * (s2[2] + e2 * (s2[3] + e2 * s2[4])));
  }
  var hi = 20;
  function ci(t2, e2, i2) {
    for (var s2 = 1 / (1 - e2), r2 = t2, a2 = hi; a2; --a2) {
      var n2 = Math.sin(r2), o2 = 1 - e2 * n2 * n2;
      if (r2 -= o2 = (oi(r2, n2, Math.cos(r2), i2) - t2) * (o2 * Math.sqrt(o2)) * s2, Math.abs(o2) < _)
        return r2;
    }
    return r2;
  }
  function li() {
    this.x0 = void 0 !== this.x0 ? this.x0 : 0, this.y0 = void 0 !== this.y0 ? this.y0 : 0, this.long0 = void 0 !== this.long0 ? this.long0 : 0, this.lat0 = void 0 !== this.lat0 ? this.lat0 : 0, this.es && (this.en = ni(this.es), this.ml0 = oi(this.lat0, Math.sin(this.lat0), Math.cos(this.lat0), this.en));
  }
  function ui(t2) {
    var e2, i2, s2, r2 = t2.x, a2 = t2.y, n2 = pt(r2 - this.long0), o2 = Math.sin(a2), h2 = Math.cos(a2);
    if (this.es) {
      var c2 = h2 * n2, l2 = Math.pow(c2, 2), u2 = this.ep2 * Math.pow(h2, 2), d2 = Math.pow(u2, 2), m2 = Math.abs(h2) > _ ? Math.tan(a2) : 0, f2 = Math.pow(m2, 2), p2 = Math.pow(f2, 2);
      e2 = 1 - this.es * Math.pow(o2, 2), c2 /= Math.sqrt(e2);
      var x2 = oi(a2, o2, h2, this.en);
      i2 = this.a * (this.k0 * c2 * (1 + l2 / 6 * (1 - f2 + u2 + l2 / 20 * (5 - 18 * f2 + p2 + 14 * u2 - 58 * f2 * u2 + l2 / 42 * (61 + 179 * p2 - p2 * f2 - 479 * f2))))) + this.x0, s2 = this.a * (this.k0 * (x2 - this.ml0 + o2 * n2 * c2 / 2 * (1 + l2 / 12 * (5 - f2 + 9 * u2 + 4 * d2 + l2 / 30 * (61 + p2 - 58 * f2 + 270 * u2 - 330 * f2 * u2 + l2 / 56 * (1385 + 543 * p2 - p2 * f2 - 3111 * f2)))))) + this.y0;
    } else {
      var y2 = h2 * Math.sin(n2);
      if (Math.abs(Math.abs(y2) - 1) < _)
        return 93;
      if (i2 = 0.5 * this.a * this.k0 * Math.log((1 + y2) / (1 - y2)) + this.x0, s2 = h2 * Math.cos(n2) / Math.sqrt(1 - Math.pow(y2, 2)), (y2 = Math.abs(s2)) >= 1) {
        if (y2 - 1 > _)
          return 93;
        s2 = 0;
      } else
        s2 = Math.acos(s2);
      a2 < 0 && (s2 = -s2), s2 = this.a * this.k0 * (s2 - this.lat0) + this.y0;
    }
    return t2.x = i2, t2.y = s2, t2;
  }
  function di(t2) {
    var e2, i2, s2, r2, a2 = (t2.x - this.x0) * (1 / this.a), n2 = (t2.y - this.y0) * (1 / this.a);
    if (this.es)
      if (i2 = ci(e2 = this.ml0 + n2 / this.k0, this.es, this.en), Math.abs(i2) < l) {
        var o2 = Math.sin(i2), h2 = Math.cos(i2), c2 = Math.abs(h2) > _ ? Math.tan(i2) : 0, u2 = this.ep2 * Math.pow(h2, 2), d2 = Math.pow(u2, 2), m2 = Math.pow(c2, 2), f2 = Math.pow(m2, 2);
        e2 = 1 - this.es * Math.pow(o2, 2);
        var p2 = a2 * Math.sqrt(e2) / this.k0, x2 = Math.pow(p2, 2);
        s2 = i2 - (e2 *= c2) * x2 / (1 - this.es) * 0.5 * (1 - x2 / 12 * (5 + 3 * m2 - 9 * u2 * m2 + u2 - 4 * d2 - x2 / 30 * (61 + 90 * m2 - 252 * u2 * m2 + 45 * f2 + 46 * u2 - x2 / 56 * (1385 + 3633 * m2 + 4095 * f2 + 1574 * f2 * m2)))), r2 = pt(this.long0 + p2 * (1 - x2 / 6 * (1 + 2 * m2 + u2 - x2 / 20 * (5 + 28 * m2 + 24 * f2 + 8 * u2 * m2 + 6 * u2 - x2 / 42 * (61 + 662 * m2 + 1320 * f2 + 720 * f2 * m2)))) / h2);
      } else
        s2 = l * ft(n2), r2 = 0;
    else {
      var y2 = Math.exp(a2 / this.k0), M2 = 0.5 * (y2 - 1 / y2), g2 = this.lat0 + n2 / this.k0, w2 = Math.cos(g2);
      e2 = Math.sqrt((1 - Math.pow(w2, 2)) / (1 + Math.pow(M2, 2))), s2 = Math.asin(e2), n2 < 0 && (s2 = -s2), r2 = 0 === M2 && 0 === w2 ? 0 : pt(Math.atan2(M2, w2) + this.long0);
    }
    return t2.x = r2, t2.y = s2, t2;
  }
  var mi = { init: li, forward: ui, inverse: di, names: ["Fast_Transverse_Mercator", "Fast Transverse Mercator"] };
  function _i(t2) {
    var e2 = Math.exp(t2);
    return e2 = (e2 - 1 / e2) / 2;
  }
  function fi(t2, e2) {
    t2 = Math.abs(t2), e2 = Math.abs(e2);
    var i2 = Math.max(t2, e2), s2 = Math.min(t2, e2) / (i2 || 1);
    return i2 * Math.sqrt(1 + Math.pow(s2, 2));
  }
  function pi(t2) {
    var e2 = 1 + t2, i2 = e2 - 1;
    return 0 === i2 ? t2 : t2 * Math.log(e2) / i2;
  }
  function xi(t2) {
    var e2 = Math.abs(t2);
    return e2 = pi(e2 * (1 + e2 / (fi(1, e2) + 1))), t2 < 0 ? -e2 : e2;
  }
  function yi(t2, e2) {
    for (var i2, s2 = 2 * Math.cos(2 * e2), r2 = t2.length - 1, a2 = t2[r2], n2 = 0; --r2 >= 0; )
      i2 = s2 * a2 - n2 + t2[r2], n2 = a2, a2 = i2;
    return e2 + i2 * Math.sin(2 * e2);
  }
  function Mi(t2, e2) {
    for (var i2, s2 = 2 * Math.cos(e2), r2 = t2.length - 1, a2 = t2[r2], n2 = 0; --r2 >= 0; )
      i2 = s2 * a2 - n2 + t2[r2], n2 = a2, a2 = i2;
    return Math.sin(e2) * i2;
  }
  function gi(t2) {
    var e2 = Math.exp(t2);
    return e2 = (e2 + 1 / e2) / 2;
  }
  function wi(t2, e2, i2) {
    for (var s2, r2, a2 = Math.sin(e2), n2 = Math.cos(e2), o2 = _i(i2), h2 = gi(i2), c2 = 2 * n2 * h2, l2 = -2 * a2 * o2, u2 = t2.length - 1, d2 = t2[u2], m2 = 0, _2 = 0, f2 = 0; --u2 >= 0; )
      s2 = _2, r2 = m2, d2 = c2 * (_2 = d2) - s2 - l2 * (m2 = f2) + t2[u2], f2 = l2 * _2 - r2 + c2 * m2;
    return [(c2 = a2 * h2) * d2 - (l2 = n2 * o2) * f2, c2 * f2 + l2 * d2];
  }
  function Pi() {
    if (!this.approx && (isNaN(this.es) || this.es <= 0))
      throw new Error('Incorrect elliptical usage. Try using the +approx option in the proj string, or PROJECTION["Fast_Transverse_Mercator"] in the WKT.');
    this.approx && (mi.init.apply(this), this.forward = mi.forward, this.inverse = mi.inverse), this.x0 = void 0 !== this.x0 ? this.x0 : 0, this.y0 = void 0 !== this.y0 ? this.y0 : 0, this.long0 = void 0 !== this.long0 ? this.long0 : 0, this.lat0 = void 0 !== this.lat0 ? this.lat0 : 0, this.cgb = [], this.cbg = [], this.utg = [], this.gtu = [];
    var t2 = this.es / (1 + Math.sqrt(1 - this.es)), e2 = t2 / (2 - t2), i2 = e2;
    this.cgb[0] = e2 * (2 + e2 * (-2 / 3 + e2 * (e2 * (116 / 45 + e2 * (26 / 45 + e2 * (-2854 / 675))) - 2))), this.cbg[0] = e2 * (e2 * (2 / 3 + e2 * (4 / 3 + e2 * (-82 / 45 + e2 * (32 / 45 + e2 * (4642 / 4725))))) - 2), i2 *= e2, this.cgb[1] = i2 * (7 / 3 + e2 * (e2 * (-227 / 45 + e2 * (2704 / 315 + e2 * (2323 / 945))) - 1.6)), this.cbg[1] = i2 * (5 / 3 + e2 * (-16 / 15 + e2 * (-13 / 9 + e2 * (904 / 315 + e2 * (-1522 / 945))))), i2 *= e2, this.cgb[2] = i2 * (56 / 15 + e2 * (-136 / 35 + e2 * (-1262 / 105 + e2 * (73814 / 2835)))), this.cbg[2] = i2 * (-26 / 15 + e2 * (34 / 21 + e2 * (1.6 + e2 * (-12686 / 2835)))), i2 *= e2, this.cgb[3] = i2 * (4279 / 630 + e2 * (-332 / 35 + e2 * (-399572 / 14175))), this.cbg[3] = i2 * (1237 / 630 + e2 * (e2 * (-24832 / 14175) - 2.4)), i2 *= e2, this.cgb[4] = i2 * (4174 / 315 + e2 * (-144838 / 6237)), this.cbg[4] = i2 * (-734 / 315 + e2 * (109598 / 31185)), i2 *= e2, this.cgb[5] = i2 * (601676 / 22275), this.cbg[5] = i2 * (444337 / 155925), i2 = Math.pow(e2, 2), this.Qn = this.k0 / (1 + e2) * (1 + i2 * (1 / 4 + i2 * (1 / 64 + i2 / 256))), this.utg[0] = e2 * (e2 * (2 / 3 + e2 * (-37 / 96 + e2 * (1 / 360 + e2 * (81 / 512 + e2 * (-96199 / 604800))))) - 0.5), this.gtu[0] = e2 * (0.5 + e2 * (-2 / 3 + e2 * (5 / 16 + e2 * (41 / 180 + e2 * (-127 / 288 + e2 * (7891 / 37800)))))), this.utg[1] = i2 * (-1 / 48 + e2 * (-1 / 15 + e2 * (437 / 1440 + e2 * (-46 / 105 + e2 * (1118711 / 3870720))))), this.gtu[1] = i2 * (13 / 48 + e2 * (e2 * (557 / 1440 + e2 * (281 / 630 + e2 * (-1983433 / 1935360))) - 0.6)), i2 *= e2, this.utg[2] = i2 * (-17 / 480 + e2 * (37 / 840 + e2 * (209 / 4480 + e2 * (-5569 / 90720)))), this.gtu[2] = i2 * (61 / 240 + e2 * (-103 / 140 + e2 * (15061 / 26880 + e2 * (167603 / 181440)))), i2 *= e2, this.utg[3] = i2 * (-4397 / 161280 + e2 * (11 / 504 + e2 * (830251 / 7257600))), this.gtu[3] = i2 * (49561 / 161280 + e2 * (-179 / 168 + e2 * (6601661 / 7257600))), i2 *= e2, this.utg[4] = i2 * (-4583 / 161280 + e2 * (108847 / 3991680)), this.gtu[4] = i2 * (34729 / 80640 + e2 * (-3418889 / 1995840)), i2 *= e2, this.utg[5] = i2 * (-20648693 / 638668800), this.gtu[5] = 0.6650675310896665 * i2;
    var s2 = yi(this.cbg, this.lat0);
    this.Zb = -this.Qn * (s2 + Mi(this.gtu, 2 * s2));
  }
  function Si(t2) {
    var e2 = pt(t2.x - this.long0), i2 = t2.y;
    i2 = yi(this.cbg, i2);
    var s2 = Math.sin(i2), r2 = Math.cos(i2), a2 = Math.sin(e2), n2 = Math.cos(e2);
    i2 = Math.atan2(s2, n2 * r2), e2 = Math.atan2(a2 * r2, fi(s2, r2 * n2)), e2 = xi(Math.tan(e2));
    var o2, h2, c2 = wi(this.gtu, 2 * i2, 2 * e2);
    return i2 += c2[0], e2 += c2[1], Math.abs(e2) <= 2.623395162778 ? (o2 = this.a * (this.Qn * e2) + this.x0, h2 = this.a * (this.Qn * i2 + this.Zb) + this.y0) : (o2 = 1 / 0, h2 = 1 / 0), t2.x = o2, t2.y = h2, t2;
  }
  function Ci(t2) {
    var e2, i2, s2 = (t2.x - this.x0) * (1 / this.a), r2 = (t2.y - this.y0) * (1 / this.a);
    if (r2 = (r2 - this.Zb) / this.Qn, s2 /= this.Qn, Math.abs(s2) <= 2.623395162778) {
      var a2 = wi(this.utg, 2 * r2, 2 * s2);
      r2 += a2[0], s2 += a2[1], s2 = Math.atan(_i(s2));
      var n2 = Math.sin(r2), o2 = Math.cos(r2), h2 = Math.sin(s2), c2 = Math.cos(s2);
      r2 = Math.atan2(n2 * c2, fi(h2, c2 * o2)), e2 = pt((s2 = Math.atan2(h2, c2 * o2)) + this.long0), i2 = yi(this.cgb, r2);
    } else
      e2 = 1 / 0, i2 = 1 / 0;
    return t2.x = e2, t2.y = i2, t2;
  }
  var Ei = { init: Pi, forward: Si, inverse: Ci, names: ["Extended_Transverse_Mercator", "Extended Transverse Mercator", "etmerc", "Transverse_Mercator", "Transverse Mercator", "Gauss Kruger", "Gauss_Kruger", "tmerc"] };
  function vi(t2, e2) {
    if (void 0 === t2) {
      if ((t2 = Math.floor(30 * (pt(e2) + Math.PI) / Math.PI) + 1) < 0)
        return 0;
      if (t2 > 60)
        return 60;
    }
    return t2;
  }
  function bi() {
    var t2 = vi(this.zone, this.long0);
    if (void 0 === t2)
      throw new Error("unknown utm zone");
    this.lat0 = 0, this.long0 = (6 * Math.abs(t2) - 183) * f, this.x0 = 5e5, this.y0 = this.utmSouth ? 1e7 : 0, this.k0 = 0.9996, Ei.init.apply(this), this.forward = Ei.forward, this.inverse = Ei.inverse;
  }
  var zi = { init: bi, names: ["Universal Transverse Mercator System", "utm"], dependsOn: "etmerc" };
  function Ti(t2, e2) {
    return Math.pow((1 - t2) / (1 + t2), e2);
  }
  var Ai = 20;
  function Oi() {
    var t2 = Math.sin(this.lat0), e2 = Math.cos(this.lat0);
    e2 *= e2, this.rc = Math.sqrt(1 - this.es) / (1 - this.es * t2 * t2), this.C = Math.sqrt(1 + this.es * e2 * e2 / (1 - this.es)), this.phic0 = Math.asin(t2 / this.C), this.ratexp = 0.5 * this.C * this.e, this.K = Math.tan(0.5 * this.phic0 + x) / (Math.pow(Math.tan(0.5 * this.lat0 + x), this.C) * Ti(this.e * t2, this.ratexp));
  }
  function Gi(t2) {
    var e2 = t2.x, i2 = t2.y;
    return t2.y = 2 * Math.atan(this.K * Math.pow(Math.tan(0.5 * i2 + x), this.C) * Ti(this.e * Math.sin(i2), this.ratexp)) - l, t2.x = this.C * e2, t2;
  }
  function Ni(t2) {
    for (var e2 = 1e-14, i2 = t2.x / this.C, s2 = t2.y, r2 = Math.pow(Math.tan(0.5 * s2 + x) / this.K, 1 / this.C), a2 = Ai; a2 > 0 && (s2 = 2 * Math.atan(r2 * Ti(this.e * Math.sin(t2.y), -0.5 * this.e)) - l, !(Math.abs(s2 - t2.y) < e2)); --a2)
      t2.y = s2;
    return a2 ? (t2.x = i2, t2.y = s2, t2) : null;
  }
  var Ii = { init: Oi, forward: Gi, inverse: Ni };
  function Ri() {
    Ii.init.apply(this), this.rc && (this.sinc0 = Math.sin(this.phic0), this.cosc0 = Math.cos(this.phic0), this.R2 = 2 * this.rc, this.title || (this.title = "Oblique Stereographic Alternative"));
  }
  function $i(t2) {
    var e2, i2, s2, r2;
    return t2.x = pt(t2.x - this.long0), Ii.forward.apply(this, [t2]), e2 = Math.sin(t2.y), i2 = Math.cos(t2.y), s2 = Math.cos(t2.x), r2 = this.k0 * this.R2 / (1 + this.sinc0 * e2 + this.cosc0 * i2 * s2), t2.x = r2 * i2 * Math.sin(t2.x), t2.y = r2 * (this.cosc0 * e2 - this.sinc0 * i2 * s2), t2.x = this.a * t2.x + this.x0, t2.y = this.a * t2.y + this.y0, t2;
  }
  function Vi(t2) {
    var e2, i2, s2, r2, a2;
    if (t2.x = (t2.x - this.x0) / this.a, t2.y = (t2.y - this.y0) / this.a, t2.x /= this.k0, t2.y /= this.k0, a2 = fi(t2.x, t2.y)) {
      var n2 = 2 * Math.atan2(a2, this.R2);
      e2 = Math.sin(n2), i2 = Math.cos(n2), r2 = Math.asin(i2 * this.sinc0 + t2.y * e2 * this.cosc0 / a2), s2 = Math.atan2(t2.x * e2, a2 * this.cosc0 * i2 - t2.y * this.sinc0 * e2);
    } else
      r2 = this.phic0, s2 = 0;
    return t2.x = s2, t2.y = r2, Ii.inverse.apply(this, [t2]), t2.x = pt(t2.x + this.long0), t2;
  }
  var Li = { init: Ri, forward: $i, inverse: Vi, names: ["Stereographic_North_Pole", "Oblique_Stereographic", "sterea", "Oblique Stereographic Alternative", "Double_Stereographic"] };
  function Bi(t2, e2, i2) {
    return e2 *= i2, Math.tan(0.5 * (l + t2)) * Math.pow((1 - e2) / (1 + e2), 0.5 * i2);
  }
  function qi() {
    this.x0 = this.x0 || 0, this.y0 = this.y0 || 0, this.lat0 = this.lat0 || 0, this.long0 = this.long0 || 0, this.coslat0 = Math.cos(this.lat0), this.sinlat0 = Math.sin(this.lat0), this.sphere ? 1 === this.k0 && !isNaN(this.lat_ts) && Math.abs(this.coslat0) <= _ && (this.k0 = 0.5 * (1 + ft(this.lat0) * Math.sin(this.lat_ts))) : (Math.abs(this.coslat0) <= _ && (this.lat0 > 0 ? this.con = 1 : this.con = -1), this.cons = Math.sqrt(Math.pow(1 + this.e, 1 + this.e) * Math.pow(1 - this.e, 1 - this.e)), 1 === this.k0 && !isNaN(this.lat_ts) && Math.abs(this.coslat0) <= _ && Math.abs(Math.cos(this.lat_ts)) > _ && (this.k0 = 0.5 * this.cons * _t(this.e, Math.sin(this.lat_ts), Math.cos(this.lat_ts)) / xt(this.e, this.con * this.lat_ts, this.con * Math.sin(this.lat_ts))), this.ms1 = _t(this.e, this.sinlat0, this.coslat0), this.X0 = 2 * Math.atan(Bi(this.lat0, this.sinlat0, this.e)) - l, this.cosX0 = Math.cos(this.X0), this.sinX0 = Math.sin(this.X0));
  }
  function ji(t2) {
    var e2, i2, s2, r2, a2, n2, o2 = t2.x, h2 = t2.y, c2 = Math.sin(h2), u2 = Math.cos(h2), d2 = pt(o2 - this.long0);
    return Math.abs(Math.abs(o2 - this.long0) - Math.PI) <= _ && Math.abs(h2 + this.lat0) <= _ ? (t2.x = NaN, t2.y = NaN, t2) : this.sphere ? (e2 = 2 * this.k0 / (1 + this.sinlat0 * c2 + this.coslat0 * u2 * Math.cos(d2)), t2.x = this.a * e2 * u2 * Math.sin(d2) + this.x0, t2.y = this.a * e2 * (this.coslat0 * c2 - this.sinlat0 * u2 * Math.cos(d2)) + this.y0, t2) : (i2 = 2 * Math.atan(Bi(h2, c2, this.e)) - l, r2 = Math.cos(i2), s2 = Math.sin(i2), Math.abs(this.coslat0) <= _ ? (a2 = xt(this.e, h2 * this.con, this.con * c2), n2 = 2 * this.a * this.k0 * a2 / this.cons, t2.x = this.x0 + n2 * Math.sin(o2 - this.long0), t2.y = this.y0 - this.con * n2 * Math.cos(o2 - this.long0), t2) : (Math.abs(this.sinlat0) < _ ? (e2 = 2 * this.a * this.k0 / (1 + r2 * Math.cos(d2)), t2.y = e2 * s2) : (e2 = 2 * this.a * this.k0 * this.ms1 / (this.cosX0 * (1 + this.sinX0 * s2 + this.cosX0 * r2 * Math.cos(d2))), t2.y = e2 * (this.cosX0 * s2 - this.sinX0 * r2 * Math.cos(d2)) + this.y0), t2.x = e2 * r2 * Math.sin(d2) + this.x0, t2));
  }
  function Fi(t2) {
    var e2, i2, s2, r2, a2;
    t2.x -= this.x0, t2.y -= this.y0;
    var n2 = Math.sqrt(t2.x * t2.x + t2.y * t2.y);
    if (this.sphere) {
      var o2 = 2 * Math.atan(n2 / (2 * this.a * this.k0));
      return e2 = this.long0, i2 = this.lat0, n2 <= _ ? (t2.x = e2, t2.y = i2, t2) : (i2 = Math.asin(Math.cos(o2) * this.sinlat0 + t2.y * Math.sin(o2) * this.coslat0 / n2), e2 = Math.abs(this.coslat0) < _ ? this.lat0 > 0 ? pt(this.long0 + Math.atan2(t2.x, -1 * t2.y)) : pt(this.long0 + Math.atan2(t2.x, t2.y)) : pt(this.long0 + Math.atan2(t2.x * Math.sin(o2), n2 * this.coslat0 * Math.cos(o2) - t2.y * this.sinlat0 * Math.sin(o2))), t2.x = e2, t2.y = i2, t2);
    }
    if (Math.abs(this.coslat0) <= _) {
      if (n2 <= _)
        return i2 = this.lat0, e2 = this.long0, t2.x = e2, t2.y = i2, t2;
      t2.x *= this.con, t2.y *= this.con, s2 = n2 * this.cons / (2 * this.a * this.k0), i2 = this.con * yt(this.e, s2), e2 = this.con * pt(this.con * this.long0 + Math.atan2(t2.x, -1 * t2.y));
    } else
      r2 = 2 * Math.atan(n2 * this.cosX0 / (2 * this.a * this.k0 * this.ms1)), e2 = this.long0, n2 <= _ ? a2 = this.X0 : (a2 = Math.asin(Math.cos(r2) * this.sinX0 + t2.y * Math.sin(r2) * this.cosX0 / n2), e2 = pt(this.long0 + Math.atan2(t2.x * Math.sin(r2), n2 * this.cosX0 * Math.cos(r2) - t2.y * this.sinX0 * Math.sin(r2)))), i2 = -1 * yt(this.e, Math.tan(0.5 * (l + a2)));
    return t2.x = e2, t2.y = i2, t2;
  }
  var ki = { init: qi, forward: ji, inverse: Fi, names: ["stere", "Stereographic_South_Pole", "Polar_Stereographic_variant_A", "Polar_Stereographic_variant_B", "Polar_Stereographic"], ssfn_: Bi };
  function Di() {
    var t2 = this.lat0;
    this.lambda0 = this.long0;
    var e2 = Math.sin(t2), i2 = this.a, s2 = 1 / this.rf, r2 = 2 * s2 - Math.pow(s2, 2), a2 = this.e = Math.sqrt(r2);
    this.R = this.k0 * i2 * Math.sqrt(1 - r2) / (1 - r2 * Math.pow(e2, 2)), this.alpha = Math.sqrt(1 + r2 / (1 - r2) * Math.pow(Math.cos(t2), 4)), this.b0 = Math.asin(e2 / this.alpha);
    var n2 = Math.log(Math.tan(Math.PI / 4 + this.b0 / 2)), o2 = Math.log(Math.tan(Math.PI / 4 + t2 / 2)), h2 = Math.log((1 + a2 * e2) / (1 - a2 * e2));
    this.K = n2 - this.alpha * o2 + this.alpha * a2 / 2 * h2;
  }
  function Ui(t2) {
    var e2 = Math.log(Math.tan(Math.PI / 4 - t2.y / 2)), i2 = this.e / 2 * Math.log((1 + this.e * Math.sin(t2.y)) / (1 - this.e * Math.sin(t2.y))), s2 = -this.alpha * (e2 + i2) + this.K, r2 = 2 * (Math.atan(Math.exp(s2)) - Math.PI / 4), a2 = this.alpha * (t2.x - this.lambda0), n2 = Math.atan(Math.sin(a2) / (Math.sin(this.b0) * Math.tan(r2) + Math.cos(this.b0) * Math.cos(a2))), o2 = Math.asin(Math.cos(this.b0) * Math.sin(r2) - Math.sin(this.b0) * Math.cos(r2) * Math.cos(a2));
    return t2.y = this.R / 2 * Math.log((1 + Math.sin(o2)) / (1 - Math.sin(o2))) + this.y0, t2.x = this.R * n2 + this.x0, t2;
  }
  function Wi(t2) {
    for (var e2 = t2.x - this.x0, i2 = t2.y - this.y0, s2 = e2 / this.R, r2 = 2 * (Math.atan(Math.exp(i2 / this.R)) - Math.PI / 4), a2 = Math.asin(Math.cos(this.b0) * Math.sin(r2) + Math.sin(this.b0) * Math.cos(r2) * Math.cos(s2)), n2 = Math.atan(Math.sin(s2) / (Math.cos(this.b0) * Math.cos(s2) - Math.sin(this.b0) * Math.tan(r2))), o2 = this.lambda0 + n2 / this.alpha, h2 = 0, c2 = a2, l2 = -1e3, u2 = 0; Math.abs(c2 - l2) > 1e-7; ) {
      if (++u2 > 20)
        return;
      h2 = 1 / this.alpha * (Math.log(Math.tan(Math.PI / 4 + a2 / 2)) - this.K) + this.e * Math.log(Math.tan(Math.PI / 4 + Math.asin(this.e * Math.sin(c2)) / 2)), l2 = c2, c2 = 2 * Math.atan(Math.exp(h2)) - Math.PI / 2;
    }
    return t2.x = o2, t2.y = c2, t2;
  }
  var Xi = { init: Di, forward: Ui, inverse: Wi, names: ["somerc"] }, Qi = 1e-7;
  function Hi(t2) {
    var e2 = ["Hotine_Oblique_Mercator", "Hotine_Oblique_Mercator_variant_A", "Hotine_Oblique_Mercator_Azimuth_Natural_Origin"], i2 = "object" == typeof t2.projName ? Object.keys(t2.projName)[0] : t2.projName;
    return "no_uoff" in t2 || "no_off" in t2 || -1 !== e2.indexOf(i2) || -1 !== e2.indexOf(zt(i2));
  }
  function Zi() {
    var t2, e2, i2, s2, r2, a2, n2, o2, h2, c2, u2, d2 = 0, m2 = 0, f2 = 0, p2 = 0, M2 = 0, g2 = 0, w2 = 0;
    this.no_off = Hi(this), this.no_rot = "no_rot" in this;
    var P2 = false;
    "alpha" in this && (P2 = true);
    var S2 = false;
    if ("rectified_grid_angle" in this && (S2 = true), P2 && (w2 = this.alpha), S2 && (d2 = this.rectified_grid_angle), P2 || S2)
      m2 = this.longc;
    else if (f2 = this.long1, M2 = this.lat1, p2 = this.long2, g2 = this.lat2, Math.abs(M2 - g2) <= Qi || (t2 = Math.abs(M2)) <= Qi || Math.abs(t2 - l) <= Qi || Math.abs(Math.abs(this.lat0) - l) <= Qi || Math.abs(Math.abs(g2) - l) <= Qi)
      throw new Error();
    var C2 = 1 - this.es;
    e2 = Math.sqrt(C2), Math.abs(this.lat0) > _ ? (o2 = Math.sin(this.lat0), i2 = Math.cos(this.lat0), t2 = 1 - this.es * o2 * o2, this.B = i2 * i2, this.B = Math.sqrt(1 + this.es * this.B * this.B / C2), this.A = this.B * this.k0 * e2 / t2, (r2 = (s2 = this.B * e2 / (i2 * Math.sqrt(t2))) * s2 - 1) <= 0 ? r2 = 0 : (r2 = Math.sqrt(r2), this.lat0 < 0 && (r2 = -r2)), this.E = r2 += s2, this.E *= Math.pow(xt(this.e, this.lat0, o2), this.B)) : (this.B = 1 / e2, this.A = this.k0, this.E = s2 = r2 = 1), P2 || S2 ? (P2 ? (u2 = Math.asin(Math.sin(w2) / s2), S2 || (d2 = w2)) : (u2 = d2, w2 = Math.asin(s2 * Math.sin(u2))), this.lam0 = m2 - Math.asin(0.5 * (r2 - 1 / r2) * Math.tan(u2)) / this.B) : (a2 = Math.pow(xt(this.e, M2, Math.sin(M2)), this.B), n2 = Math.pow(xt(this.e, g2, Math.sin(g2)), this.B), r2 = this.E / a2, h2 = (n2 - a2) / (n2 + a2), c2 = ((c2 = this.E * this.E) - n2 * a2) / (c2 + n2 * a2), (t2 = f2 - p2) < -Math.PI ? p2 -= y : t2 > Math.PI && (p2 += y), this.lam0 = pt(0.5 * (f2 + p2) - Math.atan(c2 * Math.tan(0.5 * this.B * (f2 - p2)) / h2) / this.B), u2 = Math.atan(2 * Math.sin(this.B * pt(f2 - this.lam0)) / (r2 - 1 / r2)), d2 = w2 = Math.asin(s2 * Math.sin(u2))), this.singam = Math.sin(u2), this.cosgam = Math.cos(u2), this.sinrot = Math.sin(d2), this.cosrot = Math.cos(d2), this.rB = 1 / this.B, this.ArB = this.A * this.rB, this.BrA = 1 / this.ArB, this.no_off ? this.u_0 = 0 : (this.u_0 = Math.abs(this.ArB * Math.atan(Math.sqrt(s2 * s2 - 1) / Math.cos(w2))), this.lat0 < 0 && (this.u_0 = -this.u_0)), r2 = 0.5 * u2, this.v_pole_n = this.ArB * Math.log(Math.tan(x - r2)), this.v_pole_s = this.ArB * Math.log(Math.tan(x + r2));
  }
  function Yi(t2) {
    var e2, i2, s2, r2, a2, n2, o2, h2, c2 = {};
    if (t2.x = t2.x - this.lam0, Math.abs(Math.abs(t2.y) - l) > _) {
      if (e2 = 0.5 * ((a2 = this.E / Math.pow(xt(this.e, t2.y, Math.sin(t2.y)), this.B)) - (n2 = 1 / a2)), i2 = 0.5 * (a2 + n2), r2 = Math.sin(this.B * t2.x), s2 = (e2 * this.singam - r2 * this.cosgam) / i2, Math.abs(Math.abs(s2) - 1) < _)
        throw new Error();
      h2 = 0.5 * this.ArB * Math.log((1 - s2) / (1 + s2)), n2 = Math.cos(this.B * t2.x), o2 = Math.abs(n2) < Qi ? this.A * t2.x : this.ArB * Math.atan2(e2 * this.cosgam + r2 * this.singam, n2);
    } else
      h2 = t2.y > 0 ? this.v_pole_n : this.v_pole_s, o2 = this.ArB * t2.y;
    return this.no_rot ? (c2.x = o2, c2.y = h2) : (o2 -= this.u_0, c2.x = h2 * this.cosrot + o2 * this.sinrot, c2.y = o2 * this.cosrot - h2 * this.sinrot), c2.x = this.a * c2.x + this.x0, c2.y = this.a * c2.y + this.y0, c2;
  }
  function Ji(t2) {
    var e2, i2, s2, r2, a2, n2, o2, h2 = {};
    if (t2.x = (t2.x - this.x0) * (1 / this.a), t2.y = (t2.y - this.y0) * (1 / this.a), this.no_rot ? (i2 = t2.y, e2 = t2.x) : (i2 = t2.x * this.cosrot - t2.y * this.sinrot, e2 = t2.y * this.cosrot + t2.x * this.sinrot + this.u_0), r2 = 0.5 * ((s2 = Math.exp(-this.BrA * i2)) - 1 / s2), a2 = 0.5 * (s2 + 1 / s2), o2 = ((n2 = Math.sin(this.BrA * e2)) * this.cosgam + r2 * this.singam) / a2, Math.abs(Math.abs(o2) - 1) < _)
      h2.x = 0, h2.y = o2 < 0 ? -l : l;
    else {
      if (h2.y = this.E / Math.sqrt((1 + o2) / (1 - o2)), h2.y = yt(this.e, Math.pow(h2.y, 1 / this.B)), h2.y === 1 / 0)
        throw new Error();
      h2.x = -this.rB * Math.atan2(r2 * this.cosgam - n2 * this.singam, Math.cos(this.BrA * e2));
    }
    return h2.x += this.lam0, h2;
  }
  var Ki = { init: Zi, forward: Yi, inverse: Ji, names: ["Hotine_Oblique_Mercator", "Hotine Oblique Mercator", "Hotine_Oblique_Mercator_variant_A", "Hotine_Oblique_Mercator_Variant_B", "Hotine_Oblique_Mercator_Azimuth_Natural_Origin", "Hotine_Oblique_Mercator_Two_Point_Natural_Origin", "Hotine_Oblique_Mercator_Azimuth_Center", "Oblique_Mercator", "omerc"] };
  function ts() {
    if (this.lat2 || (this.lat2 = this.lat1), this.k0 || (this.k0 = 1), this.x0 = this.x0 || 0, this.y0 = this.y0 || 0, !(Math.abs(this.lat1 + this.lat2) < _)) {
      var t2 = this.b / this.a;
      this.e = Math.sqrt(1 - t2 * t2);
      var e2 = Math.sin(this.lat1), i2 = Math.cos(this.lat1), s2 = _t(this.e, e2, i2), r2 = xt(this.e, this.lat1, e2), a2 = Math.sin(this.lat2), n2 = Math.cos(this.lat2), o2 = _t(this.e, a2, n2), h2 = xt(this.e, this.lat2, a2), c2 = Math.abs(Math.abs(this.lat0) - l) < _ ? 0 : xt(this.e, this.lat0, Math.sin(this.lat0));
      Math.abs(this.lat1 - this.lat2) > _ ? this.ns = Math.log(s2 / o2) / Math.log(r2 / h2) : this.ns = e2, isNaN(this.ns) && (this.ns = e2), this.f0 = s2 / (this.ns * Math.pow(r2, this.ns)), this.rh = this.a * this.f0 * Math.pow(c2, this.ns), this.title || (this.title = "Lambert Conformal Conic");
    }
  }
  function es(t2) {
    var e2 = t2.x, i2 = t2.y;
    Math.abs(2 * Math.abs(i2) - Math.PI) <= _ && (i2 = ft(i2) * (l - 2 * _));
    var s2, r2, a2 = Math.abs(Math.abs(i2) - l);
    if (a2 > _)
      s2 = xt(this.e, i2, Math.sin(i2)), r2 = this.a * this.f0 * Math.pow(s2, this.ns);
    else {
      if ((a2 = i2 * this.ns) <= 0)
        return null;
      r2 = 0;
    }
    var n2 = this.ns * pt(e2 - this.long0);
    return t2.x = this.k0 * (r2 * Math.sin(n2)) + this.x0, t2.y = this.k0 * (this.rh - r2 * Math.cos(n2)) + this.y0, t2;
  }
  function is(t2) {
    var e2, i2, s2, r2, a2, n2 = (t2.x - this.x0) / this.k0, o2 = this.rh - (t2.y - this.y0) / this.k0;
    this.ns > 0 ? (e2 = Math.sqrt(n2 * n2 + o2 * o2), i2 = 1) : (e2 = -Math.sqrt(n2 * n2 + o2 * o2), i2 = -1);
    var h2 = 0;
    if (0 !== e2 && (h2 = Math.atan2(i2 * n2, i2 * o2)), 0 !== e2 || this.ns > 0) {
      if (i2 = 1 / this.ns, s2 = Math.pow(e2 / (this.a * this.f0), i2), -9999 === (r2 = yt(this.e, s2)))
        return null;
    } else
      r2 = -l;
    return a2 = pt(h2 / this.ns + this.long0), t2.x = a2, t2.y = r2, t2;
  }
  var ss = { init: ts, forward: es, inverse: is, names: ["Lambert Tangential Conformal Conic Projection", "Lambert_Conformal_Conic", "Lambert_Conformal_Conic_1SP", "Lambert_Conformal_Conic_2SP", "lcc", "Lambert Conic Conformal (1SP)", "Lambert Conic Conformal (2SP)"] };
  function rs() {
    this.a = 6377397155e-3, this.es = 0.006674372230614, this.e = Math.sqrt(this.es), this.lat0 || (this.lat0 = 0.863937979737193), this.long0 || (this.long0 = 0.4334234309119251), this.k0 || (this.k0 = 0.9999), this.s45 = 0.785398163397448, this.s90 = 2 * this.s45, this.fi0 = this.lat0, this.e2 = this.es, this.e = Math.sqrt(this.e2), this.alfa = Math.sqrt(1 + this.e2 * Math.pow(Math.cos(this.fi0), 4) / (1 - this.e2)), this.uq = 1.04216856380474, this.u0 = Math.asin(Math.sin(this.fi0) / this.alfa), this.g = Math.pow((1 + this.e * Math.sin(this.fi0)) / (1 - this.e * Math.sin(this.fi0)), this.alfa * this.e / 2), this.k = Math.tan(this.u0 / 2 + this.s45) / Math.pow(Math.tan(this.fi0 / 2 + this.s45), this.alfa) * this.g, this.k1 = this.k0, this.n0 = this.a * Math.sqrt(1 - this.e2) / (1 - this.e2 * Math.pow(Math.sin(this.fi0), 2)), this.s0 = 1.37008346281555, this.n = Math.sin(this.s0), this.ro0 = this.k1 * this.n0 / Math.tan(this.s0), this.ad = this.s90 - this.uq;
  }
  function as(t2) {
    var e2, i2, s2, r2, a2, n2, o2, h2 = t2.x, c2 = t2.y, l2 = pt(h2 - this.long0);
    return e2 = Math.pow((1 + this.e * Math.sin(c2)) / (1 - this.e * Math.sin(c2)), this.alfa * this.e / 2), i2 = 2 * (Math.atan(this.k * Math.pow(Math.tan(c2 / 2 + this.s45), this.alfa) / e2) - this.s45), s2 = -l2 * this.alfa, r2 = Math.asin(Math.cos(this.ad) * Math.sin(i2) + Math.sin(this.ad) * Math.cos(i2) * Math.cos(s2)), a2 = Math.asin(Math.cos(i2) * Math.sin(s2) / Math.cos(r2)), n2 = this.n * a2, o2 = this.ro0 * Math.pow(Math.tan(this.s0 / 2 + this.s45), this.n) / Math.pow(Math.tan(r2 / 2 + this.s45), this.n), t2.y = o2 * Math.cos(n2) / 1, t2.x = o2 * Math.sin(n2) / 1, this.czech || (t2.y *= -1, t2.x *= -1), t2;
  }
  function ns(t2) {
    var e2, i2, s2, r2, a2, n2, o2, h2 = t2.x;
    t2.x = t2.y, t2.y = h2, this.czech || (t2.y *= -1, t2.x *= -1), a2 = Math.sqrt(t2.x * t2.x + t2.y * t2.y), r2 = Math.atan2(t2.y, t2.x) / Math.sin(this.s0), s2 = 2 * (Math.atan(Math.pow(this.ro0 / a2, 1 / this.n) * Math.tan(this.s0 / 2 + this.s45)) - this.s45), e2 = Math.asin(Math.cos(this.ad) * Math.sin(s2) - Math.sin(this.ad) * Math.cos(s2) * Math.cos(r2)), i2 = Math.asin(Math.cos(s2) * Math.sin(r2) / Math.cos(e2)), t2.x = this.long0 - i2 / this.alfa, n2 = e2, o2 = 0;
    var c2 = 0;
    do {
      t2.y = 2 * (Math.atan(Math.pow(this.k, -1 / this.alfa) * Math.pow(Math.tan(e2 / 2 + this.s45), 1 / this.alfa) * Math.pow((1 + this.e * Math.sin(n2)) / (1 - this.e * Math.sin(n2)), this.e / 2)) - this.s45), Math.abs(n2 - t2.y) < 1e-10 && (o2 = 1), n2 = t2.y, c2 += 1;
    } while (0 === o2 && c2 < 15);
    return c2 >= 15 ? null : t2;
  }
  var os = { init: rs, forward: as, inverse: ns, names: ["Krovak", "krovak"] };
  function hs(t2, e2, i2, s2, r2) {
    return t2 * r2 - e2 * Math.sin(2 * r2) + i2 * Math.sin(4 * r2) - s2 * Math.sin(6 * r2);
  }
  function cs(t2) {
    return 1 - 0.25 * t2 * (1 + t2 / 16 * (3 + 1.25 * t2));
  }
  function ls(t2) {
    return 0.375 * t2 * (1 + 0.25 * t2 * (1 + 0.46875 * t2));
  }
  function us(t2) {
    return 0.05859375 * t2 * t2 * (1 + 0.75 * t2);
  }
  function ds(t2) {
    return t2 * t2 * t2 * (35 / 3072);
  }
  function ms(t2, e2, i2) {
    var s2 = e2 * i2;
    return t2 / Math.sqrt(1 - s2 * s2);
  }
  function _s(t2) {
    return Math.abs(t2) < l ? t2 : t2 - ft(t2) * Math.PI;
  }
  function fs(t2, e2, i2, s2, r2) {
    var a2, n2;
    a2 = t2 / e2;
    for (var o2 = 0; o2 < 15; o2++)
      if (a2 += n2 = (t2 - (e2 * a2 - i2 * Math.sin(2 * a2) + s2 * Math.sin(4 * a2) - r2 * Math.sin(6 * a2))) / (e2 - 2 * i2 * Math.cos(2 * a2) + 4 * s2 * Math.cos(4 * a2) - 6 * r2 * Math.cos(6 * a2)), Math.abs(n2) <= 1e-10)
        return a2;
    return NaN;
  }
  function ps() {
    this.sphere || (this.e0 = cs(this.es), this.e1 = ls(this.es), this.e2 = us(this.es), this.e3 = ds(this.es), this.ml0 = this.a * hs(this.e0, this.e1, this.e2, this.e3, this.lat0));
  }
  function xs(t2) {
    var e2, i2, s2 = t2.x, r2 = t2.y;
    if (s2 = pt(s2 - this.long0), this.sphere)
      e2 = this.a * Math.asin(Math.cos(r2) * Math.sin(s2)), i2 = this.a * (Math.atan2(Math.tan(r2), Math.cos(s2)) - this.lat0);
    else {
      var a2 = Math.sin(r2), n2 = Math.cos(r2), o2 = ms(this.a, this.e, a2), h2 = Math.tan(r2) * Math.tan(r2), c2 = s2 * Math.cos(r2), l2 = c2 * c2, u2 = this.es * n2 * n2 / (1 - this.es);
      e2 = o2 * c2 * (1 - l2 * h2 * (1 / 6 - (8 - h2 + 8 * u2) * l2 / 120)), i2 = this.a * hs(this.e0, this.e1, this.e2, this.e3, r2) - this.ml0 + o2 * a2 / n2 * l2 * (0.5 + (5 - h2 + 6 * u2) * l2 / 24);
    }
    return t2.x = e2 + this.x0, t2.y = i2 + this.y0, t2;
  }
  function ys(t2) {
    t2.x -= this.x0, t2.y -= this.y0;
    var e2, i2, s2 = t2.x / this.a, r2 = t2.y / this.a;
    if (this.sphere) {
      var a2 = r2 + this.lat0;
      e2 = Math.asin(Math.sin(a2) * Math.cos(s2)), i2 = Math.atan2(Math.tan(s2), Math.cos(a2));
    } else {
      var n2 = fs(this.ml0 / this.a + r2, this.e0, this.e1, this.e2, this.e3);
      if (Math.abs(Math.abs(n2) - l) <= _)
        return t2.x = this.long0, t2.y = l, r2 < 0 && (t2.y *= -1), t2;
      var o2 = ms(this.a, this.e, Math.sin(n2)), h2 = o2 * o2 * o2 / this.a / this.a * (1 - this.es), c2 = Math.pow(Math.tan(n2), 2), u2 = s2 * this.a / o2, d2 = u2 * u2;
      e2 = n2 - o2 * Math.tan(n2) / h2 * u2 * u2 * (0.5 - (1 + 3 * c2) * u2 * u2 / 24), i2 = u2 * (1 - d2 * (c2 / 3 + (1 + 3 * c2) * c2 * d2 / 15)) / Math.cos(n2);
    }
    return t2.x = pt(i2 + this.long0), t2.y = _s(e2), t2;
  }
  var Ms = { init: ps, forward: xs, inverse: ys, names: ["Cassini", "Cassini_Soldner", "cass"] };
  function gs(t2, e2) {
    var i2;
    return t2 > 1e-7 ? (1 - t2 * t2) * (e2 / (1 - (i2 = t2 * e2) * i2) - 0.5 / t2 * Math.log((1 - i2) / (1 + i2))) : 2 * e2;
  }
  var ws = 1, Ps = 2, Ss = 3, Cs = 4;
  function Es() {
    var t2, e2 = Math.abs(this.lat0);
    if (Math.abs(e2 - l) < _ ? this.mode = this.lat0 < 0 ? ws : Ps : Math.abs(e2) < _ ? this.mode = Ss : this.mode = Cs, this.es > 0)
      switch (this.qp = gs(this.e, 1), this.mmf = 0.5 / (1 - this.es), this.apa = Is(this.es), this.mode) {
        case Ps:
        case ws:
          this.dd = 1;
          break;
        case Ss:
          this.rq = Math.sqrt(0.5 * this.qp), this.dd = 1 / this.rq, this.xmf = 1, this.ymf = 0.5 * this.qp;
          break;
        case Cs:
          this.rq = Math.sqrt(0.5 * this.qp), t2 = Math.sin(this.lat0), this.sinb1 = gs(this.e, t2) / this.qp, this.cosb1 = Math.sqrt(1 - this.sinb1 * this.sinb1), this.dd = Math.cos(this.lat0) / (Math.sqrt(1 - this.es * t2 * t2) * this.rq * this.cosb1), this.ymf = (this.xmf = this.rq) / this.dd, this.xmf *= this.dd;
      }
    else
      this.mode === Cs && (this.sinph0 = Math.sin(this.lat0), this.cosph0 = Math.cos(this.lat0));
  }
  function vs(t2) {
    var e2, i2, s2, r2, a2, n2, o2, h2, c2, u2, d2 = t2.x, m2 = t2.y;
    if (d2 = pt(d2 - this.long0), this.sphere) {
      if (a2 = Math.sin(m2), u2 = Math.cos(m2), s2 = Math.cos(d2), this.mode === this.OBLIQ || this.mode === this.EQUIT) {
        if ((i2 = this.mode === this.EQUIT ? 1 + u2 * s2 : 1 + this.sinph0 * a2 + this.cosph0 * u2 * s2) <= _)
          return null;
        e2 = (i2 = Math.sqrt(2 / i2)) * u2 * Math.sin(d2), i2 *= this.mode === this.EQUIT ? a2 : this.cosph0 * a2 - this.sinph0 * u2 * s2;
      } else if (this.mode === this.N_POLE || this.mode === this.S_POLE) {
        if (this.mode === this.N_POLE && (s2 = -s2), Math.abs(m2 + this.lat0) < _)
          return null;
        i2 = x - 0.5 * m2, e2 = (i2 = 2 * (this.mode === this.S_POLE ? Math.cos(i2) : Math.sin(i2))) * Math.sin(d2), i2 *= s2;
      }
    } else {
      switch (o2 = 0, h2 = 0, c2 = 0, s2 = Math.cos(d2), r2 = Math.sin(d2), a2 = Math.sin(m2), n2 = gs(this.e, a2), this.mode !== this.OBLIQ && this.mode !== this.EQUIT || (o2 = n2 / this.qp, h2 = Math.sqrt(1 - o2 * o2)), this.mode) {
        case this.OBLIQ:
          c2 = 1 + this.sinb1 * o2 + this.cosb1 * h2 * s2;
          break;
        case this.EQUIT:
          c2 = 1 + h2 * s2;
          break;
        case this.N_POLE:
          c2 = l + m2, n2 = this.qp - n2;
          break;
        case this.S_POLE:
          c2 = m2 - l, n2 = this.qp + n2;
      }
      if (Math.abs(c2) < _)
        return null;
      switch (this.mode) {
        case this.OBLIQ:
        case this.EQUIT:
          c2 = Math.sqrt(2 / c2), i2 = this.mode === this.OBLIQ ? this.ymf * c2 * (this.cosb1 * o2 - this.sinb1 * h2 * s2) : (c2 = Math.sqrt(2 / (1 + h2 * s2))) * o2 * this.ymf, e2 = this.xmf * c2 * h2 * r2;
          break;
        case this.N_POLE:
        case this.S_POLE:
          n2 >= 0 ? (e2 = (c2 = Math.sqrt(n2)) * r2, i2 = s2 * (this.mode === this.S_POLE ? c2 : -c2)) : e2 = i2 = 0;
      }
    }
    return t2.x = this.a * e2 + this.x0, t2.y = this.a * i2 + this.y0, t2;
  }
  function bs(t2) {
    t2.x -= this.x0, t2.y -= this.y0;
    var e2, i2, s2, r2, a2, n2, o2, h2 = t2.x / this.a, c2 = t2.y / this.a;
    if (this.sphere) {
      var u2, d2 = 0, m2 = 0;
      if ((i2 = 0.5 * (u2 = Math.sqrt(h2 * h2 + c2 * c2))) > 1)
        return null;
      switch (i2 = 2 * Math.asin(i2), this.mode !== this.OBLIQ && this.mode !== this.EQUIT || (m2 = Math.sin(i2), d2 = Math.cos(i2)), this.mode) {
        case this.EQUIT:
          i2 = Math.abs(u2) <= _ ? 0 : Math.asin(c2 * m2 / u2), h2 *= m2, c2 = d2 * u2;
          break;
        case this.OBLIQ:
          i2 = Math.abs(u2) <= _ ? this.lat0 : Math.asin(d2 * this.sinph0 + c2 * m2 * this.cosph0 / u2), h2 *= m2 * this.cosph0, c2 = (d2 - Math.sin(i2) * this.sinph0) * u2;
          break;
        case this.N_POLE:
          c2 = -c2, i2 = l - i2;
          break;
        case this.S_POLE:
          i2 -= l;
      }
      e2 = 0 !== c2 || this.mode !== this.EQUIT && this.mode !== this.OBLIQ ? Math.atan2(h2, c2) : 0;
    } else {
      if (o2 = 0, this.mode === this.OBLIQ || this.mode === this.EQUIT) {
        if (h2 /= this.dd, c2 *= this.dd, (n2 = Math.sqrt(h2 * h2 + c2 * c2)) < _)
          return t2.x = this.long0, t2.y = this.lat0, t2;
        r2 = 2 * Math.asin(0.5 * n2 / this.rq), s2 = Math.cos(r2), h2 *= r2 = Math.sin(r2), this.mode === this.OBLIQ ? (o2 = s2 * this.sinb1 + c2 * r2 * this.cosb1 / n2, a2 = this.qp * o2, c2 = n2 * this.cosb1 * s2 - c2 * this.sinb1 * r2) : (o2 = c2 * r2 / n2, a2 = this.qp * o2, c2 = n2 * s2);
      } else if (this.mode === this.N_POLE || this.mode === this.S_POLE) {
        if (this.mode === this.N_POLE && (c2 = -c2), !(a2 = h2 * h2 + c2 * c2))
          return t2.x = this.long0, t2.y = this.lat0, t2;
        o2 = 1 - a2 / this.qp, this.mode === this.S_POLE && (o2 = -o2);
      }
      e2 = Math.atan2(h2, c2), i2 = Rs(Math.asin(o2), this.apa);
    }
    return t2.x = pt(this.long0 + e2), t2.y = i2, t2;
  }
  var zs = 0.3333333333333333, Ts = 0.17222222222222222, As = 0.10257936507936508, Os = 0.06388888888888888, Gs = 0.0664021164021164, Ns = 0.016415012942191543;
  function Is(t2) {
    var e2, i2 = [];
    return i2[0] = t2 * zs, e2 = t2 * t2, i2[0] += e2 * Ts, i2[1] = e2 * Os, e2 *= t2, i2[0] += e2 * As, i2[1] += e2 * Gs, i2[2] = e2 * Ns, i2;
  }
  function Rs(t2, e2) {
    var i2 = t2 + t2;
    return t2 + e2[0] * Math.sin(i2) + e2[1] * Math.sin(i2 + i2) + e2[2] * Math.sin(i2 + i2 + i2);
  }
  var $s = { init: Es, forward: vs, inverse: bs, names: ["Lambert Azimuthal Equal Area", "Lambert_Azimuthal_Equal_Area", "laea"], S_POLE: ws, N_POLE: Ps, EQUIT: Ss, OBLIQ: Cs };
  function Vs(t2) {
    return Math.abs(t2) > 1 && (t2 = t2 > 1 ? 1 : -1), Math.asin(t2);
  }
  function Ls() {
    Math.abs(this.lat1 + this.lat2) < _ || (this.temp = this.b / this.a, this.es = 1 - Math.pow(this.temp, 2), this.e3 = Math.sqrt(this.es), this.sin_po = Math.sin(this.lat1), this.cos_po = Math.cos(this.lat1), this.t1 = this.sin_po, this.con = this.sin_po, this.ms1 = _t(this.e3, this.sin_po, this.cos_po), this.qs1 = gs(this.e3, this.sin_po), this.sin_po = Math.sin(this.lat2), this.cos_po = Math.cos(this.lat2), this.t2 = this.sin_po, this.ms2 = _t(this.e3, this.sin_po, this.cos_po), this.qs2 = gs(this.e3, this.sin_po), this.sin_po = Math.sin(this.lat0), this.cos_po = Math.cos(this.lat0), this.t3 = this.sin_po, this.qs0 = gs(this.e3, this.sin_po), Math.abs(this.lat1 - this.lat2) > _ ? this.ns0 = (this.ms1 * this.ms1 - this.ms2 * this.ms2) / (this.qs2 - this.qs1) : this.ns0 = this.con, this.c = this.ms1 * this.ms1 + this.ns0 * this.qs1, this.rh = this.a * Math.sqrt(this.c - this.ns0 * this.qs0) / this.ns0);
  }
  function Bs(t2) {
    var e2 = t2.x, i2 = t2.y;
    this.sin_phi = Math.sin(i2), this.cos_phi = Math.cos(i2);
    var s2 = gs(this.e3, this.sin_phi), r2 = this.a * Math.sqrt(this.c - this.ns0 * s2) / this.ns0, a2 = this.ns0 * pt(e2 - this.long0), n2 = r2 * Math.sin(a2) + this.x0, o2 = this.rh - r2 * Math.cos(a2) + this.y0;
    return t2.x = n2, t2.y = o2, t2;
  }
  function qs(t2) {
    var e2, i2, s2, r2, a2, n2;
    return t2.x -= this.x0, t2.y = this.rh - t2.y + this.y0, this.ns0 >= 0 ? (e2 = Math.sqrt(t2.x * t2.x + t2.y * t2.y), s2 = 1) : (e2 = -Math.sqrt(t2.x * t2.x + t2.y * t2.y), s2 = -1), r2 = 0, 0 !== e2 && (r2 = Math.atan2(s2 * t2.x, s2 * t2.y)), s2 = e2 * this.ns0 / this.a, this.sphere ? n2 = Math.asin((this.c - s2 * s2) / (2 * this.ns0)) : (i2 = (this.c - s2 * s2) / this.ns0, n2 = this.phi1z(this.e3, i2)), a2 = pt(r2 / this.ns0 + this.long0), t2.x = a2, t2.y = n2, t2;
  }
  function js(t2, e2) {
    var i2, s2, r2, a2, n2 = Vs(0.5 * e2);
    if (t2 < _)
      return n2;
    for (var o2 = t2 * t2, h2 = 1; h2 <= 25; h2++)
      if (n2 += a2 = 0.5 * (r2 = 1 - (s2 = t2 * (i2 = Math.sin(n2))) * s2) * r2 / Math.cos(n2) * (e2 / (1 - o2) - i2 / r2 + 0.5 / t2 * Math.log((1 - s2) / (1 + s2))), Math.abs(a2) <= 1e-7)
        return n2;
    return null;
  }
  var Fs = { init: Ls, forward: Bs, inverse: qs, names: ["Albers_Conic_Equal_Area", "Albers_Equal_Area", "Albers", "aea"], phi1z: js };
  function ks() {
    this.sin_p14 = Math.sin(this.lat0), this.cos_p14 = Math.cos(this.lat0), this.infinity_dist = 1e3 * this.a, this.rc = 1;
  }
  function Ds(t2) {
    var e2, i2, s2, r2, a2, n2, o2, h2, c2 = t2.x, l2 = t2.y;
    return s2 = pt(c2 - this.long0), e2 = Math.sin(l2), i2 = Math.cos(l2), r2 = Math.cos(s2), a2 = 1, (n2 = this.sin_p14 * e2 + this.cos_p14 * i2 * r2) > 0 || Math.abs(n2) <= _ ? (o2 = this.x0 + this.a * a2 * i2 * Math.sin(s2) / n2, h2 = this.y0 + this.a * a2 * (this.cos_p14 * e2 - this.sin_p14 * i2 * r2) / n2) : (o2 = this.x0 + this.infinity_dist * i2 * Math.sin(s2), h2 = this.y0 + this.infinity_dist * (this.cos_p14 * e2 - this.sin_p14 * i2 * r2)), t2.x = o2, t2.y = h2, t2;
  }
  function Us(t2) {
    var e2, i2, s2, r2, a2, n2;
    return t2.x = (t2.x - this.x0) / this.a, t2.y = (t2.y - this.y0) / this.a, t2.x /= this.k0, t2.y /= this.k0, (e2 = Math.sqrt(t2.x * t2.x + t2.y * t2.y)) ? (r2 = Math.atan2(e2, this.rc), i2 = Math.sin(r2), n2 = Vs((s2 = Math.cos(r2)) * this.sin_p14 + t2.y * i2 * this.cos_p14 / e2), a2 = Math.atan2(t2.x * i2, e2 * this.cos_p14 * s2 - t2.y * this.sin_p14 * i2), a2 = pt(this.long0 + a2)) : (n2 = this.phic0, a2 = 0), t2.x = a2, t2.y = n2, t2;
  }
  var Ws = { init: ks, forward: Ds, inverse: Us, names: ["gnom"] };
  function Xs(t2, e2) {
    var i2 = 1 - (1 - t2 * t2) / (2 * t2) * Math.log((1 - t2) / (1 + t2));
    if (Math.abs(Math.abs(e2) - i2) < 1e-6)
      return e2 < 0 ? -1 * l : l;
    for (var s2, r2, a2, n2, o2 = Math.asin(0.5 * e2), h2 = 0; h2 < 30; h2++)
      if (r2 = Math.sin(o2), a2 = Math.cos(o2), n2 = t2 * r2, o2 += s2 = Math.pow(1 - n2 * n2, 2) / (2 * a2) * (e2 / (1 - t2 * t2) - r2 / (1 - n2 * n2) + 0.5 / t2 * Math.log((1 - n2) / (1 + n2))), Math.abs(s2) <= 1e-10)
        return o2;
    return NaN;
  }
  function Qs() {
    this.sphere || (this.k0 = _t(this.e, Math.sin(this.lat_ts), Math.cos(this.lat_ts)));
  }
  function Hs(t2) {
    var e2, i2, s2 = t2.x, r2 = t2.y, a2 = pt(s2 - this.long0);
    if (this.sphere)
      e2 = this.x0 + this.a * a2 * Math.cos(this.lat_ts), i2 = this.y0 + this.a * Math.sin(r2) / Math.cos(this.lat_ts);
    else {
      var n2 = gs(this.e, Math.sin(r2));
      e2 = this.x0 + this.a * this.k0 * a2, i2 = this.y0 + this.a * n2 * 0.5 / this.k0;
    }
    return t2.x = e2, t2.y = i2, t2;
  }
  function Zs(t2) {
    var e2, i2;
    return t2.x -= this.x0, t2.y -= this.y0, this.sphere ? (e2 = pt(this.long0 + t2.x / this.a / Math.cos(this.lat_ts)), i2 = Math.asin(t2.y / this.a * Math.cos(this.lat_ts))) : (i2 = Xs(this.e, 2 * t2.y * this.k0 / this.a), e2 = pt(this.long0 + t2.x / (this.a * this.k0))), t2.x = e2, t2.y = i2, t2;
  }
  var Ys = { init: Qs, forward: Hs, inverse: Zs, names: ["cea"] };
  function Js() {
    this.x0 = this.x0 || 0, this.y0 = this.y0 || 0, this.lat0 = this.lat0 || 0, this.long0 = this.long0 || 0, this.lat_ts = this.lat_ts || 0, this.title = this.title || "Equidistant Cylindrical (Plate Carre)", this.rc = Math.cos(this.lat_ts);
  }
  function Ks(t2) {
    var e2 = t2.x, i2 = t2.y, s2 = pt(e2 - this.long0), r2 = _s(i2 - this.lat0);
    return t2.x = this.x0 + this.a * s2 * this.rc, t2.y = this.y0 + this.a * r2, t2;
  }
  function tr(t2) {
    var e2 = t2.x, i2 = t2.y;
    return t2.x = pt(this.long0 + (e2 - this.x0) / (this.a * this.rc)), t2.y = _s(this.lat0 + (i2 - this.y0) / this.a), t2;
  }
  var er = { init: Js, forward: Ks, inverse: tr, names: ["Equirectangular", "Equidistant_Cylindrical", "Equidistant_Cylindrical_Spherical", "eqc"] }, ir = 20;
  function sr() {
    this.temp = this.b / this.a, this.es = 1 - Math.pow(this.temp, 2), this.e = Math.sqrt(this.es), this.e0 = cs(this.es), this.e1 = ls(this.es), this.e2 = us(this.es), this.e3 = ds(this.es), this.ml0 = this.a * hs(this.e0, this.e1, this.e2, this.e3, this.lat0);
  }
  function rr(t2) {
    var e2, i2, s2, r2 = t2.x, a2 = t2.y, n2 = pt(r2 - this.long0);
    if (s2 = n2 * Math.sin(a2), this.sphere)
      Math.abs(a2) <= _ ? (e2 = this.a * n2, i2 = -1 * this.a * this.lat0) : (e2 = this.a * Math.sin(s2) / Math.tan(a2), i2 = this.a * (_s(a2 - this.lat0) + (1 - Math.cos(s2)) / Math.tan(a2)));
    else if (Math.abs(a2) <= _)
      e2 = this.a * n2, i2 = -1 * this.ml0;
    else {
      var o2 = ms(this.a, this.e, Math.sin(a2)) / Math.tan(a2);
      e2 = o2 * Math.sin(s2), i2 = this.a * hs(this.e0, this.e1, this.e2, this.e3, a2) - this.ml0 + o2 * (1 - Math.cos(s2));
    }
    return t2.x = e2 + this.x0, t2.y = i2 + this.y0, t2;
  }
  function ar(t2) {
    var e2, i2, s2, r2, a2, n2, o2, h2, c2;
    if (s2 = t2.x - this.x0, r2 = t2.y - this.y0, this.sphere)
      if (Math.abs(r2 + this.a * this.lat0) <= _)
        e2 = pt(s2 / this.a + this.long0), i2 = 0;
      else {
        var l2;
        for (n2 = this.lat0 + r2 / this.a, o2 = s2 * s2 / this.a / this.a + n2 * n2, h2 = n2, a2 = ir; a2; --a2)
          if (h2 += c2 = -1 * (n2 * (h2 * (l2 = Math.tan(h2)) + 1) - h2 - 0.5 * (h2 * h2 + o2) * l2) / ((h2 - n2) / l2 - 1), Math.abs(c2) <= _) {
            i2 = h2;
            break;
          }
        e2 = pt(this.long0 + Math.asin(s2 * Math.tan(h2) / this.a) / Math.sin(i2));
      }
    else if (Math.abs(r2 + this.ml0) <= _)
      i2 = 0, e2 = pt(this.long0 + s2 / this.a);
    else {
      var u2, d2, m2, f2, p2;
      for (n2 = (this.ml0 + r2) / this.a, o2 = s2 * s2 / this.a / this.a + n2 * n2, h2 = n2, a2 = ir; a2; --a2)
        if (p2 = this.e * Math.sin(h2), u2 = Math.sqrt(1 - p2 * p2) * Math.tan(h2), d2 = this.a * hs(this.e0, this.e1, this.e2, this.e3, h2), m2 = this.e0 - 2 * this.e1 * Math.cos(2 * h2) + 4 * this.e2 * Math.cos(4 * h2) - 6 * this.e3 * Math.cos(6 * h2), h2 -= c2 = (n2 * (u2 * (f2 = d2 / this.a) + 1) - f2 - 0.5 * u2 * (f2 * f2 + o2)) / (this.es * Math.sin(2 * h2) * (f2 * f2 + o2 - 2 * n2 * f2) / (4 * u2) + (n2 - f2) * (u2 * m2 - 2 / Math.sin(2 * h2)) - m2), Math.abs(c2) <= _) {
          i2 = h2;
          break;
        }
      u2 = Math.sqrt(1 - this.es * Math.pow(Math.sin(i2), 2)) * Math.tan(i2), e2 = pt(this.long0 + Math.asin(s2 * u2 / this.a) / Math.sin(i2));
    }
    return t2.x = e2, t2.y = i2, t2;
  }
  var nr = { init: sr, forward: rr, inverse: ar, names: ["Polyconic", "American_Polyconic", "poly"] };
  function or() {
    this.A = [], this.A[1] = 0.6399175073, this.A[2] = -0.1358797613, this.A[3] = 0.063294409, this.A[4] = -0.02526853, this.A[5] = 0.0117879, this.A[6] = -55161e-7, this.A[7] = 26906e-7, this.A[8] = -1333e-6, this.A[9] = 67e-5, this.A[10] = -34e-5, this.B_re = [], this.B_im = [], this.B_re[1] = 0.7557853228, this.B_im[1] = 0, this.B_re[2] = 0.249204646, this.B_im[2] = 3371507e-9, this.B_re[3] = -1541739e-9, this.B_im[3] = 0.04105856, this.B_re[4] = -0.10162907, this.B_im[4] = 0.01727609, this.B_re[5] = -0.26623489, this.B_im[5] = -0.36249218, this.B_re[6] = -0.6870983, this.B_im[6] = -1.1651967, this.C_re = [], this.C_im = [], this.C_re[1] = 1.3231270439, this.C_im[1] = 0, this.C_re[2] = -0.577245789, this.C_im[2] = -7809598e-9, this.C_re[3] = 0.508307513, this.C_im[3] = -0.112208952, this.C_re[4] = -0.15094762, this.C_im[4] = 0.18200602, this.C_re[5] = 1.01418179, this.C_im[5] = 1.64497696, this.C_re[6] = 1.9660549, this.C_im[6] = 2.5127645, this.D = [], this.D[1] = 1.5627014243, this.D[2] = 0.5185406398, this.D[3] = -0.03333098, this.D[4] = -0.1052906, this.D[5] = -0.0368594, this.D[6] = 7317e-6, this.D[7] = 0.0122, this.D[8] = 394e-5, this.D[9] = -13e-4;
  }
  function hr(t2) {
    var e2, i2 = t2.x, s2 = t2.y - this.lat0, r2 = i2 - this.long0, a2 = s2 / c * 1e-5, n2 = r2, o2 = 1, h2 = 0;
    for (e2 = 1; e2 <= 10; e2++)
      o2 *= a2, h2 += this.A[e2] * o2;
    var l2, u2 = h2, d2 = n2, m2 = 1, _2 = 0, f2 = 0, p2 = 0;
    for (e2 = 1; e2 <= 6; e2++)
      l2 = _2 * u2 + m2 * d2, m2 = m2 * u2 - _2 * d2, _2 = l2, f2 = f2 + this.B_re[e2] * m2 - this.B_im[e2] * _2, p2 = p2 + this.B_im[e2] * m2 + this.B_re[e2] * _2;
    return t2.x = p2 * this.a + this.x0, t2.y = f2 * this.a + this.y0, t2;
  }
  function cr(t2) {
    var e2, i2, s2 = t2.x, r2 = t2.y, a2 = s2 - this.x0, n2 = (r2 - this.y0) / this.a, o2 = a2 / this.a, h2 = 1, l2 = 0, u2 = 0, d2 = 0;
    for (e2 = 1; e2 <= 6; e2++)
      i2 = l2 * n2 + h2 * o2, h2 = h2 * n2 - l2 * o2, l2 = i2, u2 = u2 + this.C_re[e2] * h2 - this.C_im[e2] * l2, d2 = d2 + this.C_im[e2] * h2 + this.C_re[e2] * l2;
    for (var m2 = 0; m2 < this.iterations; m2++) {
      var _2, f2 = u2, p2 = d2, x2 = n2, y2 = o2;
      for (e2 = 2; e2 <= 6; e2++)
        _2 = p2 * u2 + f2 * d2, f2 = f2 * u2 - p2 * d2, p2 = _2, x2 += (e2 - 1) * (this.B_re[e2] * f2 - this.B_im[e2] * p2), y2 += (e2 - 1) * (this.B_im[e2] * f2 + this.B_re[e2] * p2);
      f2 = 1, p2 = 0;
      var M2 = this.B_re[1], g2 = this.B_im[1];
      for (e2 = 2; e2 <= 6; e2++)
        _2 = p2 * u2 + f2 * d2, f2 = f2 * u2 - p2 * d2, p2 = _2, M2 += e2 * (this.B_re[e2] * f2 - this.B_im[e2] * p2), g2 += e2 * (this.B_im[e2] * f2 + this.B_re[e2] * p2);
      var w2 = M2 * M2 + g2 * g2;
      u2 = (x2 * M2 + y2 * g2) / w2, d2 = (y2 * M2 - x2 * g2) / w2;
    }
    var P2 = u2, S2 = d2, C2 = 1, E2 = 0;
    for (e2 = 1; e2 <= 9; e2++)
      C2 *= P2, E2 += this.D[e2] * C2;
    var v2 = this.lat0 + E2 * c * 1e5, b2 = this.long0 + S2;
    return t2.x = b2, t2.y = v2, t2;
  }
  var lr = { init: or, forward: hr, inverse: cr, names: ["New_Zealand_Map_Grid", "nzmg"] };
  function ur() {
  }
  function dr(t2) {
    var e2 = t2.x, i2 = t2.y, s2 = pt(e2 - this.long0), r2 = this.x0 + this.a * s2, a2 = this.y0 + this.a * Math.log(Math.tan(Math.PI / 4 + i2 / 2.5)) * 1.25;
    return t2.x = r2, t2.y = a2, t2;
  }
  function mr(t2) {
    t2.x -= this.x0, t2.y -= this.y0;
    var e2 = pt(this.long0 + t2.x / this.a), i2 = 2.5 * (Math.atan(Math.exp(0.8 * t2.y / this.a)) - Math.PI / 4);
    return t2.x = e2, t2.y = i2, t2;
  }
  var _r = { init: ur, forward: dr, inverse: mr, names: ["Miller_Cylindrical", "mill"] }, fr = 20;
  function pr() {
    this.sphere ? (this.n = 1, this.m = 0, this.es = 0, this.C_y = Math.sqrt((this.m + 1) / this.n), this.C_x = this.C_y / (this.m + 1)) : this.en = ni(this.es);
  }
  function xr(t2) {
    var e2, i2, s2 = t2.x, r2 = t2.y;
    if (s2 = pt(s2 - this.long0), this.sphere) {
      if (this.m)
        for (var a2 = this.n * Math.sin(r2), n2 = fr; n2; --n2) {
          var o2 = (this.m * r2 + Math.sin(r2) - a2) / (this.m + Math.cos(r2));
          if (r2 -= o2, Math.abs(o2) < _)
            break;
        }
      else
        r2 = 1 !== this.n ? Math.asin(this.n * Math.sin(r2)) : r2;
      e2 = this.a * this.C_x * s2 * (this.m + Math.cos(r2)), i2 = this.a * this.C_y * r2;
    } else {
      var h2 = Math.sin(r2), c2 = Math.cos(r2);
      i2 = this.a * oi(r2, h2, c2, this.en), e2 = this.a * s2 * c2 / Math.sqrt(1 - this.es * h2 * h2);
    }
    return t2.x = e2, t2.y = i2, t2;
  }
  function yr(t2) {
    var e2, i2, s2;
    return t2.x -= this.x0, i2 = t2.x / this.a, t2.y -= this.y0, e2 = t2.y / this.a, this.sphere ? (e2 /= this.C_y, i2 /= this.C_x * (this.m + Math.cos(e2)), this.m ? e2 = Vs((this.m * e2 + Math.sin(e2)) / this.n) : 1 !== this.n && (e2 = Vs(Math.sin(e2) / this.n)), i2 = pt(i2 + this.long0), e2 = _s(e2)) : (e2 = ci(t2.y / this.a, this.es, this.en), (s2 = Math.abs(e2)) < l ? (s2 = Math.sin(e2), i2 = pt(this.long0 + t2.x * Math.sqrt(1 - this.es * s2 * s2) / (this.a * Math.cos(e2)))) : s2 - _ < l && (i2 = this.long0)), t2.x = i2, t2.y = e2, t2;
  }
  var Mr = { init: pr, forward: xr, inverse: yr, names: ["Sinusoidal", "sinu"] };
  function gr() {
  }
  function wr(t2) {
    for (var e2 = t2.x, i2 = t2.y, s2 = pt(e2 - this.long0), r2 = i2, a2 = Math.PI * Math.sin(i2); ; ) {
      var n2 = -(r2 + Math.sin(r2) - a2) / (1 + Math.cos(r2));
      if (r2 += n2, Math.abs(n2) < _)
        break;
    }
    r2 /= 2, Math.PI / 2 - Math.abs(i2) < _ && (s2 = 0);
    var o2 = 0.900316316158 * this.a * s2 * Math.cos(r2) + this.x0, h2 = 1.4142135623731 * this.a * Math.sin(r2) + this.y0;
    return t2.x = o2, t2.y = h2, t2;
  }
  function Pr(t2) {
    var e2, i2;
    t2.x -= this.x0, t2.y -= this.y0, i2 = t2.y / (1.4142135623731 * this.a), Math.abs(i2) > 0.999999999999 && (i2 = 0.999999999999), e2 = Math.asin(i2);
    var s2 = pt(this.long0 + t2.x / (0.900316316158 * this.a * Math.cos(e2)));
    s2 < -Math.PI && (s2 = -Math.PI), s2 > Math.PI && (s2 = Math.PI), i2 = (2 * e2 + Math.sin(2 * e2)) / Math.PI, Math.abs(i2) > 1 && (i2 = 1);
    var r2 = Math.asin(i2);
    return t2.x = s2, t2.y = r2, t2;
  }
  var Sr = { init: gr, forward: wr, inverse: Pr, names: ["Mollweide", "moll"] };
  function Cr() {
    Math.abs(this.lat1 + this.lat2) < _ || (this.lat2 = this.lat2 || this.lat1, this.temp = this.b / this.a, this.es = 1 - Math.pow(this.temp, 2), this.e = Math.sqrt(this.es), this.e0 = cs(this.es), this.e1 = ls(this.es), this.e2 = us(this.es), this.e3 = ds(this.es), this.sin_phi = Math.sin(this.lat1), this.cos_phi = Math.cos(this.lat1), this.ms1 = _t(this.e, this.sin_phi, this.cos_phi), this.ml1 = hs(this.e0, this.e1, this.e2, this.e3, this.lat1), Math.abs(this.lat1 - this.lat2) < _ ? this.ns = this.sin_phi : (this.sin_phi = Math.sin(this.lat2), this.cos_phi = Math.cos(this.lat2), this.ms2 = _t(this.e, this.sin_phi, this.cos_phi), this.ml2 = hs(this.e0, this.e1, this.e2, this.e3, this.lat2), this.ns = (this.ms1 - this.ms2) / (this.ml2 - this.ml1)), this.g = this.ml1 + this.ms1 / this.ns, this.ml0 = hs(this.e0, this.e1, this.e2, this.e3, this.lat0), this.rh = this.a * (this.g - this.ml0));
  }
  function Er(t2) {
    var e2, i2 = t2.x, s2 = t2.y;
    if (this.sphere)
      e2 = this.a * (this.g - s2);
    else {
      var r2 = hs(this.e0, this.e1, this.e2, this.e3, s2);
      e2 = this.a * (this.g - r2);
    }
    var a2 = this.ns * pt(i2 - this.long0), n2 = this.x0 + e2 * Math.sin(a2), o2 = this.y0 + this.rh - e2 * Math.cos(a2);
    return t2.x = n2, t2.y = o2, t2;
  }
  function vr(t2) {
    var e2, i2, s2, r2;
    t2.x -= this.x0, t2.y = this.rh - t2.y + this.y0, this.ns >= 0 ? (i2 = Math.sqrt(t2.x * t2.x + t2.y * t2.y), e2 = 1) : (i2 = -Math.sqrt(t2.x * t2.x + t2.y * t2.y), e2 = -1);
    var a2 = 0;
    return 0 !== i2 && (a2 = Math.atan2(e2 * t2.x, e2 * t2.y)), this.sphere ? (r2 = pt(this.long0 + a2 / this.ns), s2 = _s(this.g - i2 / this.a), t2.x = r2, t2.y = s2, t2) : (s2 = fs(this.g - i2 / this.a, this.e0, this.e1, this.e2, this.e3), r2 = pt(this.long0 + a2 / this.ns), t2.x = r2, t2.y = s2, t2);
  }
  var br = { init: Cr, forward: Er, inverse: vr, names: ["Equidistant_Conic", "eqdc"] };
  function zr() {
    this.R = this.a;
  }
  function Tr(t2) {
    var e2, i2, s2 = t2.x, r2 = t2.y, a2 = pt(s2 - this.long0);
    Math.abs(r2) <= _ && (e2 = this.x0 + this.R * a2, i2 = this.y0);
    var n2 = Vs(2 * Math.abs(r2 / Math.PI));
    (Math.abs(a2) <= _ || Math.abs(Math.abs(r2) - l) <= _) && (e2 = this.x0, i2 = r2 >= 0 ? this.y0 + Math.PI * this.R * Math.tan(0.5 * n2) : this.y0 + Math.PI * this.R * -Math.tan(0.5 * n2));
    var o2 = 0.5 * Math.abs(Math.PI / a2 - a2 / Math.PI), h2 = o2 * o2, c2 = Math.sin(n2), u2 = Math.cos(n2), d2 = u2 / (c2 + u2 - 1), m2 = d2 * d2, f2 = d2 * (2 / c2 - 1), p2 = f2 * f2, x2 = Math.PI * this.R * (o2 * (d2 - p2) + Math.sqrt(h2 * (d2 - p2) * (d2 - p2) - (p2 + h2) * (m2 - p2))) / (p2 + h2);
    a2 < 0 && (x2 = -x2), e2 = this.x0 + x2;
    var y2 = h2 + d2;
    return x2 = Math.PI * this.R * (f2 * y2 - o2 * Math.sqrt((p2 + h2) * (h2 + 1) - y2 * y2)) / (p2 + h2), i2 = r2 >= 0 ? this.y0 + x2 : this.y0 - x2, t2.x = e2, t2.y = i2, t2;
  }
  function Ar(t2) {
    var e2, i2, s2, r2, a2, n2, o2, h2, c2, l2, u2, d2;
    return t2.x -= this.x0, t2.y -= this.y0, u2 = Math.PI * this.R, a2 = (s2 = t2.x / u2) * s2 + (r2 = t2.y / u2) * r2, u2 = 3 * (r2 * r2 / (h2 = -2 * (n2 = -Math.abs(r2) * (1 + a2)) + 1 + 2 * r2 * r2 + a2 * a2) + (2 * (o2 = n2 - 2 * r2 * r2 + s2 * s2) * o2 * o2 / h2 / h2 / h2 - 9 * n2 * o2 / h2 / h2) / 27) / (c2 = (n2 - o2 * o2 / 3 / h2) / h2) / (l2 = 2 * Math.sqrt(-c2 / 3)), Math.abs(u2) > 1 && (u2 = u2 >= 0 ? 1 : -1), d2 = Math.acos(u2) / 3, i2 = t2.y >= 0 ? (-l2 * Math.cos(d2 + Math.PI / 3) - o2 / 3 / h2) * Math.PI : -(-l2 * Math.cos(d2 + Math.PI / 3) - o2 / 3 / h2) * Math.PI, e2 = Math.abs(s2) < _ ? this.long0 : pt(this.long0 + Math.PI * (a2 - 1 + Math.sqrt(1 + 2 * (s2 * s2 - r2 * r2) + a2 * a2)) / 2 / s2), t2.x = e2, t2.y = i2, t2;
  }
  var Or = { init: zr, forward: Tr, inverse: Ar, names: ["Van_der_Grinten_I", "VanDerGrinten", "Van_der_Grinten", "vandg"] };
  function Gr(t2, e2, i2, s2, r2, a2) {
    const n2 = s2 - e2, o2 = Math.atan((1 - a2) * Math.tan(t2)), h2 = Math.atan((1 - a2) * Math.tan(i2)), c2 = Math.sin(o2), l2 = Math.cos(o2), u2 = Math.sin(h2), d2 = Math.cos(h2);
    let m2, _2, f2, p2, x2, y2, M2, g2, w2, P2, S2, C2, E2, v2, b2, z2 = n2, T2 = 100;
    do {
      if (_2 = Math.sin(z2), f2 = Math.cos(z2), p2 = Math.sqrt(d2 * _2 * (d2 * _2) + (l2 * u2 - c2 * d2 * f2) * (l2 * u2 - c2 * d2 * f2)), 0 === p2)
        return { azi1: 0, s12: 0 };
      x2 = c2 * u2 + l2 * d2 * f2, y2 = Math.atan2(p2, x2), M2 = l2 * d2 * _2 / p2, g2 = 1 - M2 * M2, w2 = 0 !== g2 ? x2 - 2 * c2 * u2 / g2 : 0, P2 = a2 / 16 * g2 * (4 + a2 * (4 - 3 * g2)), m2 = z2, z2 = n2 + (1 - P2) * a2 * M2 * (y2 + P2 * p2 * (w2 + P2 * x2 * (2 * w2 * w2 - 1)));
    } while (Math.abs(z2 - m2) > 1e-12 && --T2 > 0);
    return 0 === T2 ? { azi1: NaN, s12: NaN } : (S2 = g2 * (r2 * r2 - r2 * (1 - a2) * (r2 * (1 - a2))) / (r2 * (1 - a2) * (r2 * (1 - a2))), C2 = 1 + S2 / 16384 * (4096 + S2 * (S2 * (320 - 175 * S2) - 768)), E2 = S2 / 1024 * (256 + S2 * (S2 * (74 - 47 * S2) - 128)), v2 = E2 * p2 * (w2 + E2 / 4 * (x2 * (2 * w2 * w2 - 1) - E2 / 6 * w2 * (4 * p2 * p2 - 3) * (4 * w2 * w2 - 3))), b2 = r2 * (1 - a2) * C2 * (y2 - v2), { azi1: Math.atan2(d2 * _2, l2 * u2 - c2 * d2 * f2), s12: b2 });
  }
  function Nr(t2, e2, i2, s2, r2, a2) {
    const n2 = Math.atan((1 - a2) * Math.tan(t2)), o2 = Math.sin(n2), h2 = Math.cos(n2), c2 = Math.sin(i2), l2 = Math.cos(i2), u2 = Math.atan2(o2, h2 * l2), d2 = h2 * c2, m2 = 1 - d2 * d2, _2 = m2 * (r2 * r2 - r2 * (1 - a2) * (r2 * (1 - a2))) / (r2 * (1 - a2) * (r2 * (1 - a2))), f2 = 1 + _2 / 16384 * (4096 + _2 * (_2 * (320 - 175 * _2) - 768)), p2 = _2 / 1024 * (256 + _2 * (_2 * (74 - 47 * _2) - 128));
    let x2, y2, M2, g2, w2, P2 = s2 / (r2 * (1 - a2) * f2), S2 = 100;
    do {
      y2 = Math.cos(2 * u2 + P2), M2 = Math.sin(P2), g2 = Math.cos(P2), w2 = p2 * M2 * (y2 + p2 / 4 * (g2 * (2 * y2 * y2 - 1) - p2 / 6 * y2 * (4 * M2 * M2 - 3) * (4 * y2 * y2 - 3))), x2 = P2, P2 = s2 / (r2 * (1 - a2) * f2) + w2;
    } while (Math.abs(P2 - x2) > 1e-12 && --S2 > 0);
    if (0 === S2)
      return { lat2: NaN, lon2: NaN };
    const C2 = o2 * M2 - h2 * g2 * l2, E2 = a2 / 16 * m2 * (4 + a2 * (4 - 3 * m2));
    return { lat2: Math.atan2(o2 * g2 + h2 * M2 * l2, (1 - a2) * Math.sqrt(d2 * d2 + C2 * C2)), lon2: e2 + (Math.atan2(M2 * c2, h2 * g2 - o2 * M2 * l2) - (1 - E2) * a2 * d2 * (P2 + E2 * M2 * (y2 + E2 * g2 * (2 * y2 * y2 - 1)))) };
  }
  function Ir() {
    this.sin_p12 = Math.sin(this.lat0), this.cos_p12 = Math.cos(this.lat0), this.f = this.es / (1 + Math.sqrt(1 - this.es));
  }
  function Rr(t2) {
    var e2, i2, s2, r2, a2, n2, o2, h2, c2, u2, d2, m2 = t2.x, f2 = t2.y, p2 = Math.sin(t2.y), x2 = Math.cos(t2.y), y2 = pt(m2 - this.long0);
    return this.sphere ? Math.abs(this.sin_p12 - 1) <= _ ? (t2.x = this.x0 + this.a * (l - f2) * Math.sin(y2), t2.y = this.y0 - this.a * (l - f2) * Math.cos(y2), t2) : Math.abs(this.sin_p12 + 1) <= _ ? (t2.x = this.x0 + this.a * (l + f2) * Math.sin(y2), t2.y = this.y0 + this.a * (l + f2) * Math.cos(y2), t2) : (c2 = this.sin_p12 * p2 + this.cos_p12 * x2 * Math.cos(y2), h2 = (o2 = Math.acos(c2)) ? o2 / Math.sin(o2) : 1, t2.x = this.x0 + this.a * h2 * x2 * Math.sin(y2), t2.y = this.y0 + this.a * h2 * (this.cos_p12 * p2 - this.sin_p12 * x2 * Math.cos(y2)), t2) : (e2 = cs(this.es), i2 = ls(this.es), s2 = us(this.es), r2 = ds(this.es), Math.abs(this.sin_p12 - 1) <= _ ? (a2 = this.a * hs(e2, i2, s2, r2, l), n2 = this.a * hs(e2, i2, s2, r2, f2), t2.x = this.x0 + (a2 - n2) * Math.sin(y2), t2.y = this.y0 - (a2 - n2) * Math.cos(y2), t2) : Math.abs(this.sin_p12 + 1) <= _ ? (a2 = this.a * hs(e2, i2, s2, r2, l), n2 = this.a * hs(e2, i2, s2, r2, f2), t2.x = this.x0 + (a2 + n2) * Math.sin(y2), t2.y = this.y0 + (a2 + n2) * Math.cos(y2), t2) : Math.abs(m2) < _ && Math.abs(f2 - this.lat0) < _ ? (t2.x = t2.y = 0, t2) : (d2 = (u2 = Gr(this.lat0, this.long0, f2, m2, this.a, this.f)).azi1, t2.x = u2.s12 * Math.sin(d2), t2.y = u2.s12 * Math.cos(d2), t2));
  }
  function $r(t2) {
    var e2, i2, s2, r2, a2, n2, o2, h2, c2, u2, d2, m2, f2, p2, x2;
    if (t2.x -= this.x0, t2.y -= this.y0, this.sphere) {
      if ((e2 = Math.sqrt(t2.x * t2.x + t2.y * t2.y)) > 2 * l * this.a)
        return;
      return i2 = e2 / this.a, s2 = Math.sin(i2), r2 = Math.cos(i2), a2 = this.long0, Math.abs(e2) <= _ ? n2 = this.lat0 : (n2 = Vs(r2 * this.sin_p12 + t2.y * s2 * this.cos_p12 / e2), o2 = Math.abs(this.lat0) - l, a2 = Math.abs(o2) <= _ ? this.lat0 >= 0 ? pt(this.long0 + Math.atan2(t2.x, -t2.y)) : pt(this.long0 - Math.atan2(-t2.x, t2.y)) : pt(this.long0 + Math.atan2(t2.x * s2, e2 * this.cos_p12 * r2 - t2.y * this.sin_p12 * s2))), t2.x = a2, t2.y = n2, t2;
    }
    return h2 = cs(this.es), c2 = ls(this.es), u2 = us(this.es), d2 = ds(this.es), Math.abs(this.sin_p12 - 1) <= _ ? (n2 = fs(((m2 = this.a * hs(h2, c2, u2, d2, l)) - (e2 = Math.sqrt(t2.x * t2.x + t2.y * t2.y))) / this.a, h2, c2, u2, d2), a2 = pt(this.long0 + Math.atan2(t2.x, -1 * t2.y)), t2.x = a2, t2.y = n2, t2) : Math.abs(this.sin_p12 + 1) <= _ ? (m2 = this.a * hs(h2, c2, u2, d2, l), n2 = fs(((e2 = Math.sqrt(t2.x * t2.x + t2.y * t2.y)) - m2) / this.a, h2, c2, u2, d2), a2 = pt(this.long0 + Math.atan2(t2.x, t2.y)), t2.x = a2, t2.y = n2, t2) : (f2 = Math.atan2(t2.x, t2.y), p2 = Math.sqrt(t2.x * t2.x + t2.y * t2.y), x2 = Nr(this.lat0, this.long0, f2, p2, this.a, this.f), t2.x = x2.lon2, t2.y = x2.lat2, t2);
  }
  var Vr = { init: Ir, forward: Rr, inverse: $r, names: ["Azimuthal_Equidistant", "aeqd"] };
  function Lr() {
    this.sin_p14 = Math.sin(this.lat0), this.cos_p14 = Math.cos(this.lat0);
  }
  function Br(t2) {
    var e2, i2, s2, r2, a2, n2, o2, h2, c2 = t2.x, l2 = t2.y;
    return s2 = pt(c2 - this.long0), e2 = Math.sin(l2), i2 = Math.cos(l2), r2 = Math.cos(s2), a2 = 1, ((n2 = this.sin_p14 * e2 + this.cos_p14 * i2 * r2) > 0 || Math.abs(n2) <= _) && (o2 = this.a * a2 * i2 * Math.sin(s2), h2 = this.y0 + this.a * a2 * (this.cos_p14 * e2 - this.sin_p14 * i2 * r2)), t2.x = o2, t2.y = h2, t2;
  }
  function qr(t2) {
    var e2, i2, s2, r2, a2, n2, o2;
    return t2.x -= this.x0, t2.y -= this.y0, i2 = Vs((e2 = Math.sqrt(t2.x * t2.x + t2.y * t2.y)) / this.a), s2 = Math.sin(i2), r2 = Math.cos(i2), n2 = this.long0, Math.abs(e2) <= _ ? (o2 = this.lat0, t2.x = n2, t2.y = o2, t2) : (o2 = Vs(r2 * this.sin_p14 + t2.y * s2 * this.cos_p14 / e2), a2 = Math.abs(this.lat0) - l, Math.abs(a2) <= _ ? (n2 = this.lat0 >= 0 ? pt(this.long0 + Math.atan2(t2.x, -t2.y)) : pt(this.long0 - Math.atan2(-t2.x, t2.y)), t2.x = n2, t2.y = o2, t2) : (n2 = pt(this.long0 + Math.atan2(t2.x * s2, e2 * this.cos_p14 * r2 - t2.y * this.sin_p14 * s2)), t2.x = n2, t2.y = o2, t2));
  }
  var jr = { init: Lr, forward: Br, inverse: qr, names: ["ortho"] }, Fr = { FRONT: 1, RIGHT: 2, BACK: 3, LEFT: 4, TOP: 5, BOTTOM: 6 }, kr = { AREA_0: 1, AREA_1: 2, AREA_2: 3, AREA_3: 4 };
  function Dr() {
    this.x0 = this.x0 || 0, this.y0 = this.y0 || 0, this.lat0 = this.lat0 || 0, this.long0 = this.long0 || 0, this.lat_ts = this.lat_ts || 0, this.title = this.title || "Quadrilateralized Spherical Cube", this.lat0 >= l - x / 2 ? this.face = Fr.TOP : this.lat0 <= -(l - x / 2) ? this.face = Fr.BOTTOM : Math.abs(this.long0) <= x ? this.face = Fr.FRONT : Math.abs(this.long0) <= l + x ? this.face = this.long0 > 0 ? Fr.RIGHT : Fr.LEFT : this.face = Fr.BACK, 0 !== this.es && (this.one_minus_f = 1 - (this.a - this.b) / this.a, this.one_minus_f_squared = this.one_minus_f * this.one_minus_f);
  }
  function Ur(t2) {
    var e2, i2, s2, r2, a2, n2, o2 = { x: 0, y: 0 }, h2 = { value: 0 };
    if (t2.x -= this.long0, e2 = 0 !== this.es ? Math.atan(this.one_minus_f_squared * Math.tan(t2.y)) : t2.y, i2 = t2.x, this.face === Fr.TOP)
      r2 = l - e2, i2 >= x && i2 <= l + x ? (h2.value = kr.AREA_0, s2 = i2 - l) : i2 > l + x || i2 <= -(l + x) ? (h2.value = kr.AREA_1, s2 = i2 > 0 ? i2 - M : i2 + M) : i2 > -(l + x) && i2 <= -x ? (h2.value = kr.AREA_2, s2 = i2 + l) : (h2.value = kr.AREA_3, s2 = i2);
    else if (this.face === Fr.BOTTOM)
      r2 = l + e2, i2 >= x && i2 <= l + x ? (h2.value = kr.AREA_0, s2 = -i2 + l) : i2 < x && i2 >= -x ? (h2.value = kr.AREA_1, s2 = -i2) : i2 < -x && i2 >= -(l + x) ? (h2.value = kr.AREA_2, s2 = -i2 - l) : (h2.value = kr.AREA_3, s2 = i2 > 0 ? -i2 + M : -i2 - M);
    else {
      var c2, u2, d2, m2, _2, f2;
      this.face === Fr.RIGHT ? i2 = Qr(i2, +l) : this.face === Fr.BACK ? i2 = Qr(i2, 3.14159265359) : this.face === Fr.LEFT && (i2 = Qr(i2, -l)), m2 = Math.sin(e2), _2 = Math.cos(e2), f2 = Math.sin(i2), c2 = _2 * Math.cos(i2), u2 = _2 * f2, d2 = m2, this.face === Fr.FRONT ? s2 = Xr(r2 = Math.acos(c2), d2, u2, h2) : this.face === Fr.RIGHT ? s2 = Xr(r2 = Math.acos(u2), d2, -c2, h2) : this.face === Fr.BACK ? s2 = Xr(r2 = Math.acos(-c2), d2, -u2, h2) : this.face === Fr.LEFT ? s2 = Xr(r2 = Math.acos(-u2), d2, c2, h2) : (r2 = s2 = 0, h2.value = kr.AREA_0);
    }
    return n2 = Math.atan(12 / M * (s2 + Math.acos(Math.sin(s2) * Math.cos(x)) - l)), a2 = Math.sqrt((1 - Math.cos(r2)) / (Math.cos(n2) * Math.cos(n2)) / (1 - Math.cos(Math.atan(1 / Math.cos(s2))))), h2.value === kr.AREA_1 ? n2 += l : h2.value === kr.AREA_2 ? n2 += M : h2.value === kr.AREA_3 && (n2 += 1.5 * M), o2.x = a2 * Math.cos(n2), o2.y = a2 * Math.sin(n2), o2.x = o2.x * this.a + this.x0, o2.y = o2.y * this.a + this.y0, t2.x = o2.x, t2.y = o2.y, t2;
  }
  function Wr(t2) {
    var e2, i2, s2, r2, a2, n2, o2, h2, c2, u2, d2, m2, _2 = { lam: 0, phi: 0 }, f2 = { value: 0 };
    if (t2.x = (t2.x - this.x0) / this.a, t2.y = (t2.y - this.y0) / this.a, i2 = Math.atan(Math.sqrt(t2.x * t2.x + t2.y * t2.y)), e2 = Math.atan2(t2.y, t2.x), t2.x >= 0 && t2.x >= Math.abs(t2.y) ? f2.value = kr.AREA_0 : t2.y >= 0 && t2.y >= Math.abs(t2.x) ? (f2.value = kr.AREA_1, e2 -= l) : t2.x < 0 && -t2.x >= Math.abs(t2.y) ? (f2.value = kr.AREA_2, e2 = e2 < 0 ? e2 + M : e2 - M) : (f2.value = kr.AREA_3, e2 += l), c2 = M / 12 * Math.tan(e2), a2 = Math.sin(c2) / (Math.cos(c2) - 1 / Math.sqrt(2)), n2 = Math.atan(a2), (o2 = 1 - (s2 = Math.cos(e2)) * s2 * (r2 = Math.tan(i2)) * r2 * (1 - Math.cos(Math.atan(1 / Math.cos(n2))))) < -1 ? o2 = -1 : o2 > 1 && (o2 = 1), this.face === Fr.TOP)
      h2 = Math.acos(o2), _2.phi = l - h2, f2.value === kr.AREA_0 ? _2.lam = n2 + l : f2.value === kr.AREA_1 ? _2.lam = n2 < 0 ? n2 + M : n2 - M : f2.value === kr.AREA_2 ? _2.lam = n2 - l : _2.lam = n2;
    else if (this.face === Fr.BOTTOM)
      h2 = Math.acos(o2), _2.phi = h2 - l, f2.value === kr.AREA_0 ? _2.lam = -n2 + l : f2.value === kr.AREA_1 ? _2.lam = -n2 : f2.value === kr.AREA_2 ? _2.lam = -n2 - l : _2.lam = n2 < 0 ? -n2 - M : -n2 + M;
    else {
      var p2, x2, y2;
      c2 = (p2 = o2) * p2, x2 = (c2 += (y2 = c2 >= 1 ? 0 : Math.sqrt(1 - c2) * Math.sin(n2)) * y2) >= 1 ? 0 : Math.sqrt(1 - c2), f2.value === kr.AREA_1 ? (c2 = x2, x2 = -y2, y2 = c2) : f2.value === kr.AREA_2 ? (x2 = -x2, y2 = -y2) : f2.value === kr.AREA_3 && (c2 = x2, x2 = y2, y2 = -c2), this.face === Fr.RIGHT ? (c2 = p2, p2 = -x2, x2 = c2) : this.face === Fr.BACK ? (p2 = -p2, x2 = -x2) : this.face === Fr.LEFT && (c2 = p2, p2 = x2, x2 = -c2), _2.phi = Math.acos(-y2) - l, _2.lam = Math.atan2(x2, p2), this.face === Fr.RIGHT ? _2.lam = Qr(_2.lam, -l) : this.face === Fr.BACK ? _2.lam = Qr(_2.lam, -3.14159265359) : this.face === Fr.LEFT && (_2.lam = Qr(_2.lam, +l));
    }
    return 0 !== this.es && (u2 = _2.phi < 0 ? 1 : 0, d2 = Math.tan(_2.phi), m2 = this.b / Math.sqrt(d2 * d2 + this.one_minus_f_squared), _2.phi = Math.atan(Math.sqrt(this.a * this.a - m2 * m2) / (this.one_minus_f * m2)), u2 && (_2.phi = -_2.phi)), _2.lam += this.long0, t2.x = _2.lam, t2.y = _2.phi, t2;
  }
  function Xr(t2, e2, i2, s2) {
    var r2;
    return t2 < _ ? (s2.value = kr.AREA_0, r2 = 0) : (r2 = Math.atan2(e2, i2), Math.abs(r2) <= x ? s2.value = kr.AREA_0 : r2 > x && r2 <= l + x ? (s2.value = kr.AREA_1, r2 -= l) : r2 > l + x || r2 <= -(l + x) ? (s2.value = kr.AREA_2, r2 = r2 >= 0 ? r2 - M : r2 + M) : (s2.value = kr.AREA_3, r2 += l)), r2;
  }
  function Qr(t2, e2) {
    var i2 = t2 + e2;
    return i2 < -3.14159265359 ? i2 += y : i2 > 3.14159265359 && (i2 -= y), i2;
  }
  var Hr = { init: Dr, forward: Ur, inverse: Wr, names: ["Quadrilateralized Spherical Cube", "Quadrilateralized_Spherical_Cube", "qsc"] }, Zr = [[1, 22199e-21, -715515e-10, 31103e-10], [0.9986, -482243e-9, -24897e-9, -13309e-10], [0.9954, -83103e-8, -448605e-10, -986701e-12], [0.99, -135364e-8, -59661e-9, 36777e-10], [0.9822, -167442e-8, -449547e-11, -572411e-11], [0.973, -214868e-8, -903571e-10, 18736e-12], [0.96, -305085e-8, -900761e-10, 164917e-11], [0.9427, -382792e-8, -653386e-10, -26154e-10], [0.9216, -467746e-8, -10457e-8, 481243e-11], [0.8962, -536223e-8, -323831e-10, -543432e-11], [0.8679, -609363e-8, -113898e-9, 332484e-11], [0.835, -698325e-8, -640253e-10, 934959e-12], [0.7986, -755338e-8, -500009e-10, 935324e-12], [0.7597, -798324e-8, -35971e-9, -227626e-11], [0.7186, -851367e-8, -701149e-10, -86303e-10], [0.6732, -986209e-8, -199569e-9, 191974e-10], [0.6213, -0.010418, 883923e-10, 624051e-11], [0.5722, -906601e-8, 182e-6, 624051e-11], [0.5322, -677797e-8, 275608e-9, 624051e-11]], Yr = [[-520417e-23, 0.0124, 121431e-23, -845284e-16], [0.062, 0.0124, -126793e-14, 422642e-15], [0.124, 0.0124, 507171e-14, -160604e-14], [0.186, 0.0123999, -190189e-13, 600152e-14], [0.248, 0.0124002, 710039e-13, -224e-10], [0.31, 0.0123992, -264997e-12, 835986e-13], [0.372, 0.0124029, 988983e-12, -311994e-12], [0.434, 0.0123893, -369093e-11, -435621e-12], [0.4958, 0.0123198, -102252e-10, -345523e-12], [0.5571, 0.0121916, -154081e-10, -582288e-12], [0.6176, 0.0119938, -241424e-10, -525327e-12], [0.6769, 0.011713, -320223e-10, -516405e-12], [0.7346, 0.0113541, -397684e-10, -609052e-12], [0.7903, 0.0109107, -489042e-10, -104739e-11], [0.8435, 0.0103431, -64615e-9, -140374e-14], [0.8936, 969686e-8, -64636e-9, -8547e-9], [0.9394, 840947e-8, -192841e-9, -42106e-10], [0.9761, 616527e-8, -256e-6, -42106e-10], [1, 328947e-8, -319159e-9, -42106e-10]], Jr = 0.8487, Kr = 1.3523, ta = p / 5, ea = 1 / ta, ia = 18, sa = function(t2, e2) {
    return t2[0] + e2 * (t2[1] + e2 * (t2[2] + e2 * t2[3]));
  }, ra = function(t2, e2) {
    return t2[1] + e2 * (2 * t2[2] + 3 * e2 * t2[3]);
  };
  function aa(t2, e2, i2, s2) {
    for (var r2 = e2; s2; --s2) {
      var a2 = t2(r2);
      if (r2 -= a2, Math.abs(a2) < i2)
        break;
    }
    return r2;
  }
  function na() {
    this.x0 = this.x0 || 0, this.y0 = this.y0 || 0, this.long0 = this.long0 || 0, this.es = 0, this.title = this.title || "Robinson";
  }
  function oa(t2) {
    var e2 = pt(t2.x - this.long0), i2 = Math.abs(t2.y), s2 = Math.floor(i2 * ta);
    s2 < 0 ? s2 = 0 : s2 >= ia && (s2 = ia - 1), i2 = p * (i2 - ea * s2);
    var r2 = { x: sa(Zr[s2], i2) * e2, y: sa(Yr[s2], i2) };
    return t2.y < 0 && (r2.y = -r2.y), r2.x = r2.x * this.a * Jr + this.x0, r2.y = r2.y * this.a * Kr + this.y0, r2;
  }
  function ha(t2) {
    var e2 = { x: (t2.x - this.x0) / (this.a * Jr), y: Math.abs(t2.y - this.y0) / (this.a * Kr) };
    if (e2.y >= 1)
      e2.x /= Zr[ia][0], e2.y = t2.y < 0 ? -l : l;
    else {
      var i2 = Math.floor(e2.y * ia);
      for (i2 < 0 ? i2 = 0 : i2 >= ia && (i2 = ia - 1); ; )
        if (Yr[i2][0] > e2.y)
          --i2;
        else {
          if (!(Yr[i2 + 1][0] <= e2.y))
            break;
          ++i2;
        }
      var s2 = Yr[i2], r2 = 5 * (e2.y - s2[0]) / (Yr[i2 + 1][0] - s2[0]);
      r2 = aa(function(t3) {
        return (sa(s2, t3) - e2.y) / ra(s2, t3);
      }, r2, _, 100), e2.x /= sa(Zr[i2], r2), e2.y = (5 * i2 + r2) * f, t2.y < 0 && (e2.y = -e2.y);
    }
    return e2.x = pt(e2.x + this.long0), e2;
  }
  var ca = { init: na, forward: oa, inverse: ha, names: ["Robinson", "robin"] };
  function la() {
    this.name = "geocent";
  }
  function ua(t2) {
    return se(t2, this.es, this.a);
  }
  function da(t2) {
    return re(t2, this.es, this.a, this.b);
  }
  var ma = { init: la, forward: ua, inverse: da, names: ["Geocentric", "geocentric", "geocent", "Geocent"] }, _a = { N_POLE: 0, S_POLE: 1, EQUIT: 2, OBLIQ: 3 }, fa = { h: { def: 1e5, num: true }, azi: { def: 0, num: true, degrees: true }, tilt: { def: 0, num: true, degrees: true }, long0: { def: 0, num: true }, lat0: { def: 0, num: true } };
  function pa() {
    if (Object.keys(fa).forEach(function(t3) {
      if (void 0 === this[t3])
        this[t3] = fa[t3].def;
      else {
        if (fa[t3].num && isNaN(this[t3]))
          throw new Error("Invalid parameter value, must be numeric " + t3 + " = " + this[t3]);
        fa[t3].num && (this[t3] = parseFloat(this[t3]));
      }
      fa[t3].degrees && (this[t3] = this[t3] * f);
    }.bind(this)), Math.abs(Math.abs(this.lat0) - l) < _ ? this.mode = this.lat0 < 0 ? _a.S_POLE : _a.N_POLE : Math.abs(this.lat0) < _ ? this.mode = _a.EQUIT : (this.mode = _a.OBLIQ, this.sinph0 = Math.sin(this.lat0), this.cosph0 = Math.cos(this.lat0)), this.pn1 = this.h / this.a, this.pn1 <= 0 || this.pn1 > 1e10)
      throw new Error("Invalid height");
    this.p = 1 + this.pn1, this.rp = 1 / this.p, this.h1 = 1 / this.pn1, this.pfact = (this.p + 1) * this.h1, this.es = 0;
    var t2 = this.tilt, e2 = this.azi;
    this.cg = Math.cos(e2), this.sg = Math.sin(e2), this.cw = Math.cos(t2), this.sw = Math.sin(t2);
  }
  function xa(t2) {
    t2.x -= this.long0;
    var e2, i2, s2, r2, a2 = Math.sin(t2.y), n2 = Math.cos(t2.y), o2 = Math.cos(t2.x);
    switch (this.mode) {
      case _a.OBLIQ:
        i2 = this.sinph0 * a2 + this.cosph0 * n2 * o2;
        break;
      case _a.EQUIT:
        i2 = n2 * o2;
        break;
      case _a.S_POLE:
        i2 = -a2;
        break;
      case _a.N_POLE:
        i2 = a2;
    }
    switch (e2 = (i2 = this.pn1 / (this.p - i2)) * n2 * Math.sin(t2.x), this.mode) {
      case _a.OBLIQ:
        i2 *= this.cosph0 * a2 - this.sinph0 * n2 * o2;
        break;
      case _a.EQUIT:
        i2 *= a2;
        break;
      case _a.N_POLE:
        i2 *= -n2 * o2;
        break;
      case _a.S_POLE:
        i2 *= n2 * o2;
    }
    return r2 = 1 / ((s2 = i2 * this.cg + e2 * this.sg) * this.sw * this.h1 + this.cw), e2 = (e2 * this.cg - i2 * this.sg) * this.cw * r2, i2 = s2 * r2, t2.x = e2 * this.a, t2.y = i2 * this.a, t2;
  }
  function ya(t2) {
    t2.x /= this.a, t2.y /= this.a;
    var e2, i2, s2, r2 = { x: t2.x, y: t2.y };
    s2 = 1 / (this.pn1 - t2.y * this.sw), e2 = this.pn1 * t2.x * s2, i2 = this.pn1 * t2.y * this.cw * s2, t2.x = e2 * this.cg + i2 * this.sg, t2.y = i2 * this.cg - e2 * this.sg;
    var a2 = fi(t2.x, t2.y);
    if (Math.abs(a2) < _)
      r2.x = 0, r2.y = t2.y;
    else {
      var n2, o2;
      switch (o2 = 1 - a2 * a2 * this.pfact, o2 = (this.p - Math.sqrt(o2)) / (this.pn1 / a2 + a2 / this.pn1), n2 = Math.sqrt(1 - o2 * o2), this.mode) {
        case _a.OBLIQ:
          r2.y = Math.asin(n2 * this.sinph0 + t2.y * o2 * this.cosph0 / a2), t2.y = (n2 - this.sinph0 * Math.sin(r2.y)) * a2, t2.x *= o2 * this.cosph0;
          break;
        case _a.EQUIT:
          r2.y = Math.asin(t2.y * o2 / a2), t2.y = n2 * a2, t2.x *= o2;
          break;
        case _a.N_POLE:
          r2.y = Math.asin(n2), t2.y = -t2.y;
          break;
        case _a.S_POLE:
          r2.y = -Math.asin(n2);
      }
      r2.x = Math.atan2(t2.x, t2.y);
    }
    return t2.x = r2.x + this.long0, t2.y = r2.y, t2;
  }
  var Ma = { init: pa, forward: xa, inverse: ya, names: ["Tilted_Perspective", "tpers"] };
  function ga() {
    if (this.flip_axis = "x" === this.sweep ? 1 : 0, this.h = Number(this.h), this.radius_g_1 = this.h / this.a, this.radius_g_1 <= 0 || this.radius_g_1 > 1e10)
      throw new Error();
    if (this.radius_g = 1 + this.radius_g_1, this.C = this.radius_g * this.radius_g - 1, 0 !== this.es) {
      var t2 = 1 - this.es, e2 = 1 / t2;
      this.radius_p = Math.sqrt(t2), this.radius_p2 = t2, this.radius_p_inv2 = e2, this.shape = "ellipse";
    } else
      this.radius_p = 1, this.radius_p2 = 1, this.radius_p_inv2 = 1, this.shape = "sphere";
    this.title || (this.title = "Geostationary Satellite View");
  }
  function wa(t2) {
    var e2, i2, s2, r2, a2 = t2.x, n2 = t2.y;
    if (a2 -= this.long0, "ellipse" === this.shape) {
      n2 = Math.atan(this.radius_p2 * Math.tan(n2));
      var o2 = this.radius_p / fi(this.radius_p * Math.cos(n2), Math.sin(n2));
      if (i2 = o2 * Math.cos(a2) * Math.cos(n2), s2 = o2 * Math.sin(a2) * Math.cos(n2), r2 = o2 * Math.sin(n2), (this.radius_g - i2) * i2 - s2 * s2 - r2 * r2 * this.radius_p_inv2 < 0)
        return t2.x = Number.NaN, t2.y = Number.NaN, t2;
      e2 = this.radius_g - i2, this.flip_axis ? (t2.x = this.radius_g_1 * Math.atan(s2 / fi(r2, e2)), t2.y = this.radius_g_1 * Math.atan(r2 / e2)) : (t2.x = this.radius_g_1 * Math.atan(s2 / e2), t2.y = this.radius_g_1 * Math.atan(r2 / fi(s2, e2)));
    } else
      "sphere" === this.shape && (e2 = Math.cos(n2), i2 = Math.cos(a2) * e2, s2 = Math.sin(a2) * e2, r2 = Math.sin(n2), e2 = this.radius_g - i2, this.flip_axis ? (t2.x = this.radius_g_1 * Math.atan(s2 / fi(r2, e2)), t2.y = this.radius_g_1 * Math.atan(r2 / e2)) : (t2.x = this.radius_g_1 * Math.atan(s2 / e2), t2.y = this.radius_g_1 * Math.atan(r2 / fi(s2, e2))));
    return t2.x = t2.x * this.a, t2.y = t2.y * this.a, t2;
  }
  function Pa(t2) {
    var e2, i2, s2, r2, a2 = -1, n2 = 0, o2 = 0;
    if (t2.x = t2.x / this.a, t2.y = t2.y / this.a, "ellipse" === this.shape) {
      this.flip_axis ? (o2 = Math.tan(t2.y / this.radius_g_1), n2 = Math.tan(t2.x / this.radius_g_1) * fi(1, o2)) : (n2 = Math.tan(t2.x / this.radius_g_1), o2 = Math.tan(t2.y / this.radius_g_1) * fi(1, n2));
      var h2 = o2 / this.radius_p;
      if (e2 = n2 * n2 + h2 * h2 + a2 * a2, (s2 = (i2 = 2 * this.radius_g * a2) * i2 - 4 * e2 * this.C) < 0)
        return t2.x = Number.NaN, t2.y = Number.NaN, t2;
      r2 = (-i2 - Math.sqrt(s2)) / (2 * e2), a2 = this.radius_g + r2 * a2, n2 *= r2, o2 *= r2, t2.x = Math.atan2(n2, a2), t2.y = Math.atan(o2 * Math.cos(t2.x) / a2), t2.y = Math.atan(this.radius_p_inv2 * Math.tan(t2.y));
    } else if ("sphere" === this.shape) {
      if (this.flip_axis ? (o2 = Math.tan(t2.y / this.radius_g_1), n2 = Math.tan(t2.x / this.radius_g_1) * Math.sqrt(1 + o2 * o2)) : (n2 = Math.tan(t2.x / this.radius_g_1), o2 = Math.tan(t2.y / this.radius_g_1) * Math.sqrt(1 + n2 * n2)), e2 = n2 * n2 + o2 * o2 + a2 * a2, (s2 = (i2 = 2 * this.radius_g * a2) * i2 - 4 * e2 * this.C) < 0)
        return t2.x = Number.NaN, t2.y = Number.NaN, t2;
      r2 = (-i2 - Math.sqrt(s2)) / (2 * e2), a2 = this.radius_g + r2 * a2, n2 *= r2, o2 *= r2, t2.x = Math.atan2(n2, a2), t2.y = Math.atan(o2 * Math.cos(t2.x) / a2);
    }
    return t2.x = t2.x + this.long0, t2;
  }
  var Sa = { init: ga, forward: wa, inverse: Pa, names: ["Geostationary Satellite View", "Geostationary_Satellite", "geos"] }, Ca = 1.340264, Ea = -0.081106, va = 893e-6, ba = 3796e-6, za = Math.sqrt(3) / 2;
  function Ta() {
    this.es = 0, this.long0 = void 0 !== this.long0 ? this.long0 : 0;
  }
  function Aa(t2) {
    var e2 = pt(t2.x - this.long0), i2 = t2.y, s2 = Math.asin(za * Math.sin(i2)), r2 = s2 * s2, a2 = r2 * r2 * r2;
    return t2.x = e2 * Math.cos(s2) / (za * (Ca + 3 * Ea * r2 + a2 * (7 * va + 9 * ba * r2))), t2.y = s2 * (Ca + Ea * r2 + a2 * (va + ba * r2)), t2.x = this.a * t2.x + this.x0, t2.y = this.a * t2.y + this.y0, t2;
  }
  function Oa(t2) {
    t2.x = (t2.x - this.x0) / this.a, t2.y = (t2.y - this.y0) / this.a;
    var e2, i2, s2, r2, a2 = 1e-9, n2 = 12, o2 = t2.y;
    for (r2 = 0; r2 < n2 && (o2 -= s2 = (o2 * (Ca + Ea * (e2 = o2 * o2) + (i2 = e2 * e2 * e2) * (va + ba * e2)) - t2.y) / (Ca + 3 * Ea * e2 + i2 * (7 * va + 9 * ba * e2)), !(Math.abs(s2) < a2)); ++r2)
      ;
    return i2 = (e2 = o2 * o2) * e2 * e2, t2.x = za * t2.x * (Ca + 3 * Ea * e2 + i2 * (7 * va + 9 * ba * e2)) / Math.cos(o2), t2.y = Math.asin(Math.sin(o2) / za), t2.x = pt(t2.x + this.long0), t2;
  }
  var Ga = { init: Ta, forward: Aa, inverse: Oa, names: ["eqearth", "Equal Earth", "Equal_Earth"] }, Na = 1e-10;
  function Ia() {
    var t2;
    if (this.phi1 = this.lat1, Math.abs(this.phi1) < Na)
      throw new Error();
    this.es ? (this.en = ni(this.es), this.m1 = oi(this.phi1, this.am1 = Math.sin(this.phi1), t2 = Math.cos(this.phi1), this.en), this.am1 = t2 / (Math.sqrt(1 - this.es * this.am1 * this.am1) * this.am1), this.inverse = $a, this.forward = Ra) : (Math.abs(this.phi1) + Na >= l ? this.cphi1 = 0 : this.cphi1 = 1 / Math.tan(this.phi1), this.inverse = La, this.forward = Va);
  }
  function Ra(t2) {
    var e2, i2, s2, r2 = pt(t2.x - (this.long0 || 0)), a2 = t2.y;
    return e2 = this.am1 + this.m1 - oi(a2, i2 = Math.sin(a2), s2 = Math.cos(a2), this.en), i2 = s2 * r2 / (e2 * Math.sqrt(1 - this.es * i2 * i2)), t2.x = e2 * Math.sin(i2), t2.y = this.am1 - e2 * Math.cos(i2), t2.x = this.a * t2.x + (this.x0 || 0), t2.y = this.a * t2.y + (this.y0 || 0), t2;
  }
  function $a(t2) {
    var e2, i2, s2, r2;
    if (t2.x = (t2.x - (this.x0 || 0)) / this.a, t2.y = (t2.y - (this.y0 || 0)) / this.a, i2 = fi(t2.x, t2.y = this.am1 - t2.y), r2 = ci(this.am1 + this.m1 - i2, this.es, this.en), (e2 = Math.abs(r2)) < l)
      e2 = Math.sin(r2), s2 = i2 * Math.atan2(t2.x, t2.y) * Math.sqrt(1 - this.es * e2 * e2) / Math.cos(r2);
    else {
      if (!(Math.abs(e2 - l) <= Na))
        throw new Error();
      s2 = 0;
    }
    return t2.x = pt(s2 + (this.long0 || 0)), t2.y = _s(r2), t2;
  }
  function Va(t2) {
    var e2, i2, s2 = pt(t2.x - (this.long0 || 0)), r2 = t2.y;
    return i2 = this.cphi1 + this.phi1 - r2, Math.abs(i2) > Na ? (t2.x = i2 * Math.sin(e2 = s2 * Math.cos(r2) / i2), t2.y = this.cphi1 - i2 * Math.cos(e2)) : t2.x = t2.y = 0, t2.x = this.a * t2.x + (this.x0 || 0), t2.y = this.a * t2.y + (this.y0 || 0), t2;
  }
  function La(t2) {
    var e2, i2;
    t2.x = (t2.x - (this.x0 || 0)) / this.a, t2.y = (t2.y - (this.y0 || 0)) / this.a;
    var s2 = fi(t2.x, t2.y = this.cphi1 - t2.y);
    if (i2 = this.cphi1 + this.phi1 - s2, Math.abs(i2) > l)
      throw new Error();
    return e2 = Math.abs(Math.abs(i2) - l) <= Na ? 0 : s2 * Math.atan2(t2.x, t2.y) / Math.cos(i2), t2.x = pt(e2 + (this.long0 || 0)), t2.y = _s(i2), t2;
  }
  var Ba = { init: Ia, names: ["bonne", "Bonne (Werner lat_1=90)"] };
  function qa(t2) {
    t2.Proj.projections.add(mi), t2.Proj.projections.add(Ei), t2.Proj.projections.add(zi), t2.Proj.projections.add(Li), t2.Proj.projections.add(ki), t2.Proj.projections.add(Xi), t2.Proj.projections.add(Ki), t2.Proj.projections.add(ss), t2.Proj.projections.add(os), t2.Proj.projections.add(Ms), t2.Proj.projections.add($s), t2.Proj.projections.add(Fs), t2.Proj.projections.add(Ws), t2.Proj.projections.add(Ys), t2.Proj.projections.add(er), t2.Proj.projections.add(nr), t2.Proj.projections.add(lr), t2.Proj.projections.add(_r), t2.Proj.projections.add(Mr), t2.Proj.projections.add(Sr), t2.Proj.projections.add(br), t2.Proj.projections.add(Or), t2.Proj.projections.add(Vr), t2.Proj.projections.add(jr), t2.Proj.projections.add(Hr), t2.Proj.projections.add(ca), t2.Proj.projections.add(ma), t2.Proj.projections.add(Ma), t2.Proj.projections.add(Sa), t2.Proj.projections.add(Ga), t2.Proj.projections.add(Ba);
  }
  const ja = Object.assign(we, { defaultDatum: "WGS84", Proj: ee, WGS84: new ee("WGS84"), Point: Xe, toPoint: me, defs: rt, nadgrid: jt, transform: xe, mgrs: Ae, version: "2.19.7" });
  return qa(ja), ja;
}();
var proj4 = proj4Src.exports;
let globalId = 1;
const _inputArray = new Array(2);
class GenericDefinedProjection extends Projection {
  constructor(t, e, i) {
    super(), t || (t = "custom_" + globalId++), this._name = t, this._parameters = e, proj4.defs(this._name) || proj4.defs(this._name, e), i && i.isBox3 && (this._geoBoundingBox = i);
  }
  projectCoordinate(t, e, i) {
    _inputArray[0] = t.x, _inputArray[1] = t.y;
    const s = this.geoBoundingBox;
    _inputArray[0] < s.min.x && (_inputArray[0] = s.min.x), _inputArray[0] > s.max.x && (_inputArray[0] = s.max.x), _inputArray[1] < s.min.y && (_inputArray[1] = s.min.y), _inputArray[1] > s.max.y && (_inputArray[1] = s.max.y);
    const r = proj4("EPSG:4326", this.name, _inputArray);
    if (e || (e = new Vector3$1()), i) {
      const i2 = this.projectedBoundingBox;
      e.x = extendProjectCoordinate(t.x, r[0], s.max.x, i2.max.x), e.y = extendProjectCoordinate(t.y, r[1], s.max.y, i2.max.y);
    } else
      e.x = r[0], e.y = r[1];
    return e.z = t.z, e;
  }
  unprojectCoordinate(t, e, i) {
    const s = this.projectedBoundingBox;
    _inputArray[0] = t.x, _inputArray[1] = t.y, _inputArray[0] < s.min.x && (_inputArray[0] = s.min.x), _inputArray[0] > s.max.x && (_inputArray[0] = s.max.x), _inputArray[1] < s.min.y && (_inputArray[1] = s.min.y), _inputArray[1] > s.max.y && (_inputArray[1] = s.max.y);
    const r = proj4(this.name, "EPSG:4326", _inputArray);
    if (e || (e = new Vector3$1()), i) {
      const i2 = this.geoBoundingBox;
      e.x = extendUnprojectCoordinate(t.x, r[0], i2.max.x, s.max.x), e.y = extendUnprojectCoordinate(t.y, r[1], i2.max.y, s.max.y);
    } else
      e.x = r[0], e.y = r[1];
    return e.z = t.z, e;
  }
  get name() {
    return this._name;
  }
}
const _cache = {}, projectionDefs = { "EPSG:5070": { parameters: "+proj=aea +lat_0=23 +lon_0=-96 +lat_1=29.5 +lat_2=45.5 +x_0=0 +y_0=0 +ellps=GRS80 +towgs84=0,0,0,0,0,0,0 +units=m +no_defs +type=crs", projectBoundingBoxMethod: projectBoundingBoxMethods.FOUR_CORNERS_WITH_EQUATOR, geoBoundingBox: [[-172.54, 23.81, 0], [-47.74, 86.46, 0]] }, "EPSG:8857": { parameters: "+proj=eqearth +lon_0=0 +x_0=0 +y_0=0 +datum=WGS84 +units=m +no_defs +type=crs", projectBoundingBoxMethod: projectBoundingBoxMethods.FOUR_CORNERS_WITH_EQUATOR } };
for (let t = 1; t <= 60; t++) {
  let e = 6 * (t - 1) - 180, i = 6 * t - 180;
  e -= 12, i += 12, e < -180 && (e = -180), i > 180 && (i = 180), projectionDefs[`EPSG:${32600 + t}`] = { projectBoundingBoxMethod: projectBoundingBoxMethods.FOUR_CORNERS_WITH_EQUATOR, geoBoundingBox: [[e, -80, 0], [i, 84, 0]] }, projectionDefs[`EPSG:${32700 + t}`] = { projectBoundingBoxMethod: projectBoundingBoxMethods.FOUR_CORNERS_WITH_EQUATOR, geoBoundingBox: [[e, -80, 0], [i, 84, 0]] };
}
for (let t = 0; t < 11; t++) {
  let e = t + 13, i = 6 * e - 3, s = 1e6 * e + 5e5;
  projectionDefs[`EPSG:${4491 + t}`] = { parameters: `+proj=tmerc +lat_0=0 +lon_0=${i} +k=1 +x_0=${s} +y_0=0 +ellps=GRS80 +units=m +no_defs +type=crs`, projectBoundingBoxMethod: projectBoundingBoxMethods.FOUR_CORNERS_WITH_EQUATOR, geoBoundingBox: [[i - 3, 3, 0], [i + 3, 54, 0]] }, projectionDefs[`EPSG:${4502 + t}`] = { parameters: `+proj=tmerc +lat_0=0 +lon_0=${i} +k=1 +x_0=500000 +y_0=0 +ellps=GRS80 +units=m +no_defs +type=crs`, projectBoundingBoxMethod: projectBoundingBoxMethods.FOUR_CORNERS_WITH_EQUATOR, geoBoundingBox: [[i - 3, 3, 0], [i + 3, 54, 0]] }, e = 25 + 2 * t, i = 3 * e, s = 1e6 * e + 5e5, projectionDefs["EPSG:" + (4513 + 2 * t)] = { parameters: `+proj=tmerc +lat_0=0 +lon_0=${i} +k=1 +x_0=${s} +y_0=0 +ellps=GRS80 +units=m +no_defs +type=crs`, projectBoundingBoxMethod: projectBoundingBoxMethods.FOUR_CORNERS_WITH_EQUATOR, geoBoundingBox: [[i - 1.5, 3, 0], [i + 1.5, 54, 0]] }, projectionDefs["EPSG:" + (4534 + 2 * t)] = { parameters: `+proj=tmerc +lat_0=0 +lon_0=${i} +k=1 +x_0=500000 +y_0=0 +ellps=GRS80 +units=m +no_defs +type=crs`, projectBoundingBoxMethod: projectBoundingBoxMethods.FOUR_CORNERS_WITH_EQUATOR, geoBoundingBox: [[i - 1.5, 3, 0], [i + 1.5, 54, 0]] }, e = 25 + 2 * t + 1, t < 10 && (i = 3 * e, s = 1e6 * e + 5e5, projectionDefs["EPSG:" + (4513 + 2 * t + 1)] = { parameters: `+proj=tmerc +lat_0=0 +lon_0=${i} +k=1 +x_0=${s} +y_0=0 +ellps=GRS80 +units=m +no_defs +type=crs`, projectBoundingBoxMethod: projectBoundingBoxMethods.FOUR_CORNERS_WITH_EQUATOR, geoBoundingBox: [[i - 1.5, 3, 0], [i + 1.5, 54, 0]] }, projectionDefs["EPSG:" + (4534 + 2 * t + 1)] = { parameters: `+proj=tmerc +lat_0=0 +lon_0=${i} +k=1 +x_0=500000 +y_0=0 +ellps=GRS80 +units=m +no_defs +type=crs`, projectBoundingBoxMethod: projectBoundingBoxMethods.FOUR_CORNERS_WITH_EQUATOR, geoBoundingBox: [[i - 1.5, 3, 0], [i + 1.5, 54, 0]] });
}
const normalizeProjectionName = (t) => "EPSG:900913" === (t = t.toUpperCase().trim()) ? PROJECTION_WEB_MERCATOR : "GLOBE" === t || "ECEF" === t ? PROJECTION_ECEF : t, getProjection = (t) => {
  if (t = normalizeProjectionName(t), !_cache[t])
    switch (t) {
      case PROJECTION_WEB_MERCATOR:
        _cache[t] = new WebMercatorProjection();
        break;
      case PROJECTION_ECEF:
        _cache[t] = new ECEFProjection();
        break;
      case PROJECTION_BD_MERCATOR:
        _cache[t] = new BaiduMercatorProjection();
        break;
      case PROJECTION_GEO:
        _cache[t] = new GeoProjection();
        break;
      case PROJECTION_SCREEN_PIXEL:
        _cache[t] = new ScreenPixelProjection();
        break;
      default:
        let e = null;
        if (projectionDefs[t]) {
          const i = projectionDefs[t];
          let s = null;
          if (i.geoBoundingBox) {
            const t2 = i.geoBoundingBox;
            s = new Box3(new Vector3$1(t2[0][0], t2[0][1], t2[0][2] || 0), new Vector3$1(t2[1][0], t2[1][1], t2[1][2] || 0));
          }
          e = new GenericDefinedProjection(t, i.parameters, s), i.projectBoundingBoxMethod && (e.projectBoundingBoxMethod = i.projectBoundingBoxMethod);
        }
        if (!e)
          throw new Error(`Unsupported projection: ${t}`);
        _cache[t] = e;
    }
  return _cache[t];
};
let tileIndex = null;
function createTileIndex(t, e) {
  try {
    return geojsonvt(t, e);
  } catch (t2) {
    return console.error("Create GeoJSON tile index error:", t2), null;
  }
}
function getTileData(t, e, i) {
  return tileIndex && tileIndex.getTile(t, e, i) || { features: [] };
}
function processTileData(t, e) {
  const i = t.features, s = { polygons: [] };
  for (let t2 = 0; t2 < i.length; t2++) {
    const r = i[t2].geometry;
    if (r.length > 0) {
      const t3 = [], i2 = r[0].map((t4) => [...normalizeCoord(t4[0], t4[1]), 0]);
      for (let e2 = 0; e2 < i2.length; e2++)
        t3.push(0, 0, 1);
      const a = earcut$1.exports.flatten([i2]), n = earcut$1.exports(a.vertices, a.holes, a.dimensions);
      projectVertices(a.vertices, e);
      const o = { vertices: a.vertices, normals: t3, indices: n };
      s.polygons.push(o);
    }
  }
  return mergePrimitive(s);
}
function mergePrimitive(t) {
  const e = {};
  return t.polygons && t.polygons.length > 0 && (e.polygon = mergePolygons(t.polygons)), e;
}
function mergePolygons(t, e = {}) {
  const i = [], s = [];
  let r, a = null, n = null, o = null, h = 0;
  const c = [Math.random(), Math.random(), Math.random(), 0.5];
  for (let e2 = 0; e2 < t.length; e2++)
    if (r = t[e2], a = r.vertices, n = r.indices, o = r.normals, n && n.length) {
      for (let t2 = 0, e3 = a.length - 2; t2 < e3; t2 += 3)
        i.push(a[t2], a[t2 + 1], a[t2 + 2]), i.push(o[t2], o[t2 + 1], o[t2 + 2]), i.push(...c);
      for (let t2 = 0, e3 = n.length; t2 < e3; t2++)
        s.push(n[t2] + h);
      h += a.length / 3;
    }
  return { attributes: new Float32Array(i), indices: new Uint32Array(s) };
}
function normalizeCoord(t, e) {
  return [t / 8192 - 0.5, -(e / 8192 - 0.5)];
}
self.onmessage = function(t) {
  const e = t.data;
  switch (e.type) {
    case "init":
      tileIndex = createTileIndex(e.data, { maxZoom: e.maxZoom || 25, minZoom: e.minZoom || 6, tolerance: e.tolerance || 6, extent: 8192, buffer: 0 });
      break;
    case "requestTile":
      const { tileKey: t2, fetchOptions: i = {}, workerOptions: s, url: r, z: a, sourceProjectionName: n, targetProjectionName: o } = e;
      e.sourceProjection = getProjection(n), e.targetProjection = getProjection(o);
      const h = o === PROJECTION_WEB_MERCATOR && n === PROJECTION_WEB_MERCATOR, c = processTileData(getTileData(e.z, e.x, e.reverseY), e);
      self.postMessage({ type: "responseTile", tileKey: t2, content: c, isNormalized: h, id: e.id });
  }
};
