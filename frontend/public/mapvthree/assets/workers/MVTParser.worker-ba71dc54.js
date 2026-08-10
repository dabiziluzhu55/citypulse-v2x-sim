var __defProp = Object.defineProperty;
var __defNormalProp = (obj, key, value) => key in obj ? __defProp(obj, key, { enumerable: true, configurable: true, writable: true, value }) : obj[key] = value;
var __publicField = (obj, key, value) => {
  __defNormalProp(obj, typeof key !== "symbol" ? key + "" : key, value);
  return value;
};
var commonjsGlobal = "undefined" != typeof globalThis ? globalThis : "undefined" != typeof window ? window : "undefined" != typeof global ? global : "undefined" != typeof self ? self : {}, pointGeometry = Point$2;
function Point$2(t, e) {
  this.x = t, this.y = e;
}
Point$2.prototype = { clone: function() {
  return new Point$2(this.x, this.y);
}, add: function(t) {
  return this.clone()._add(t);
}, sub: function(t) {
  return this.clone()._sub(t);
}, multByPoint: function(t) {
  return this.clone()._multByPoint(t);
}, divByPoint: function(t) {
  return this.clone()._divByPoint(t);
}, mult: function(t) {
  return this.clone()._mult(t);
}, div: function(t) {
  return this.clone()._div(t);
}, rotate: function(t) {
  return this.clone()._rotate(t);
}, rotateAround: function(t, e) {
  return this.clone()._rotateAround(t, e);
}, matMult: function(t) {
  return this.clone()._matMult(t);
}, unit: function() {
  return this.clone()._unit();
}, perp: function() {
  return this.clone()._perp();
}, round: function() {
  return this.clone()._round();
}, mag: function() {
  return Math.sqrt(this.x * this.x + this.y * this.y);
}, equals: function(t) {
  return this.x === t.x && this.y === t.y;
}, dist: function(t) {
  return Math.sqrt(this.distSqr(t));
}, distSqr: function(t) {
  var e = t.x - this.x, r = t.y - this.y;
  return e * e + r * r;
}, angle: function() {
  return Math.atan2(this.y, this.x);
}, angleTo: function(t) {
  return Math.atan2(this.y - t.y, this.x - t.x);
}, angleWith: function(t) {
  return this.angleWithSep(t.x, t.y);
}, angleWithSep: function(t, e) {
  return Math.atan2(this.x * e - this.y * t, this.x * t + this.y * e);
}, _matMult: function(t) {
  var e = t[0] * this.x + t[1] * this.y, r = t[2] * this.x + t[3] * this.y;
  return this.x = e, this.y = r, this;
}, _add: function(t) {
  return this.x += t.x, this.y += t.y, this;
}, _sub: function(t) {
  return this.x -= t.x, this.y -= t.y, this;
}, _mult: function(t) {
  return this.x *= t, this.y *= t, this;
}, _div: function(t) {
  return this.x /= t, this.y /= t, this;
}, _multByPoint: function(t) {
  return this.x *= t.x, this.y *= t.y, this;
}, _divByPoint: function(t) {
  return this.x /= t.x, this.y /= t.y, this;
}, _unit: function() {
  return this._div(this.mag()), this;
}, _perp: function() {
  var t = this.y;
  return this.y = this.x, this.x = -t, this;
}, _rotate: function(t) {
  var e = Math.cos(t), r = Math.sin(t), i = e * this.x - r * this.y, s = r * this.x + e * this.y;
  return this.x = i, this.y = s, this;
}, _rotateAround: function(t, e) {
  var r = Math.cos(t), i = Math.sin(t), s = e.x + r * (this.x - e.x) - i * (this.y - e.y), n = e.y + i * (this.x - e.x) + r * (this.y - e.y);
  return this.x = s, this.y = n, this;
}, _round: function() {
  return this.x = Math.round(this.x), this.y = Math.round(this.y), this;
} }, Point$2.convert = function(t) {
  return t instanceof Point$2 ? t : Array.isArray(t) ? new Point$2(t[0], t[1]) : t;
};
var Point$1 = pointGeometry, vectortilefeature = VectorTileFeature$1;
function VectorTileFeature$1(t, e, r, i, s) {
  this.properties = {}, this.extent = r, this.type = 0, this._pbf = t, this._geometry = -1, this._keys = i, this._values = s, t.readFields(readFeature, this, e);
}
function readFeature(t, e, r) {
  1 == t ? e.id = r.readVarint() : 2 == t ? readTag(r, e) : 3 == t ? e.type = r.readVarint() : 4 == t && (e._geometry = r.pos);
}
function readTag(t, e) {
  for (var r = t.readVarint() + t.pos; t.pos < r; ) {
    var i = e._keys[t.readVarint()], s = e._values[t.readVarint()];
    e.properties[i] = s;
  }
}
function classifyRings$1(t) {
  var e = t.length;
  if (e <= 1)
    return [t];
  for (var r, i, s = [], n = 0; n < e; n++) {
    var a = signedArea$1(t[n]);
    0 !== a && (void 0 === i && (i = a < 0), i === a < 0 ? (r && s.push(r), r = [t[n]]) : r.push(t[n]));
  }
  return r && s.push(r), s;
}
function signedArea$1(t) {
  for (var e, r, i = 0, s = 0, n = t.length, a = n - 1; s < n; a = s++)
    e = t[s], i += ((r = t[a]).x - e.x) * (e.y + r.y);
  return i;
}
VectorTileFeature$1.types = ["Unknown", "Point", "LineString", "Polygon"], VectorTileFeature$1.prototype.loadGeometry = function() {
  var t = this._pbf;
  t.pos = this._geometry;
  for (var e, r = t.readVarint() + t.pos, i = 1, s = 0, n = 0, a = 0, o = []; t.pos < r; ) {
    if (s <= 0) {
      var h = t.readVarint();
      i = 7 & h, s = h >> 3;
    }
    if (s--, 1 === i || 2 === i)
      n += t.readSVarint(), a += t.readSVarint(), 1 === i && (e && o.push(e), e = []), e.push(new Point$1(n, a));
    else {
      if (7 !== i)
        throw new Error("unknown command " + i);
      e && e.push(e[0].clone());
    }
  }
  return e && o.push(e), o;
}, VectorTileFeature$1.prototype.bbox = function() {
  var t = this._pbf;
  t.pos = this._geometry;
  for (var e = t.readVarint() + t.pos, r = 1, i = 0, s = 0, n = 0, a = 1 / 0, o = -1 / 0, h = 1 / 0, l = -1 / 0; t.pos < e; ) {
    if (i <= 0) {
      var c = t.readVarint();
      r = 7 & c, i = c >> 3;
    }
    if (i--, 1 === r || 2 === r)
      (s += t.readSVarint()) < a && (a = s), s > o && (o = s), (n += t.readSVarint()) < h && (h = n), n > l && (l = n);
    else if (7 !== r)
      throw new Error("unknown command " + r);
  }
  return [a, h, o, l];
}, VectorTileFeature$1.prototype.toGeoJSON = function(t, e, r) {
  var i, s, n = this.extent * Math.pow(2, r), a = this.extent * t, o = this.extent * e, h = this.loadGeometry(), l = VectorTileFeature$1.types[this.type];
  function c(t4) {
    for (var e2 = 0; e2 < t4.length; e2++) {
      var r2 = t4[e2], i2 = 180 - 360 * (r2.y + o) / n;
      t4[e2] = [360 * (r2.x + a) / n - 180, 360 / Math.PI * Math.atan(Math.exp(i2 * Math.PI / 180)) - 90];
    }
  }
  switch (this.type) {
    case 1:
      var u = [];
      for (i = 0; i < h.length; i++)
        u[i] = h[i][0];
      c(h = u);
      break;
    case 2:
      for (i = 0; i < h.length; i++)
        c(h[i]);
      break;
    case 3:
      for (h = classifyRings$1(h), i = 0; i < h.length; i++)
        for (s = 0; s < h[i].length; s++)
          c(h[i][s]);
  }
  1 === h.length ? h = h[0] : l = "Multi" + l;
  var d = { type: "Feature", geometry: { type: l, coordinates: h }, properties: this.properties };
  return "id" in this && (d.id = this.id), d;
};
var VectorTileFeature = vectortilefeature, vectortilelayer = VectorTileLayer$1;
function VectorTileLayer$1(t, e) {
  this.version = 1, this.name = null, this.extent = 4096, this.length = 0, this._pbf = t, this._keys = [], this._values = [], this._features = [], t.readFields(readLayer, this, e), this.length = this._features.length;
}
function readLayer(t, e, r) {
  15 === t ? e.version = r.readVarint() : 1 === t ? e.name = r.readString() : 5 === t ? e.extent = r.readVarint() : 2 === t ? e._features.push(r.pos) : 3 === t ? e._keys.push(r.readString()) : 4 === t && e._values.push(readValueMessage(r));
}
function readValueMessage(t) {
  for (var e = null, r = t.readVarint() + t.pos; t.pos < r; ) {
    var i = t.readVarint() >> 3;
    e = 1 === i ? t.readString() : 2 === i ? t.readFloat() : 3 === i ? t.readDouble() : 4 === i ? t.readVarint64() : 5 === i ? t.readVarint() : 6 === i ? t.readSVarint() : 7 === i ? t.readBoolean() : null;
  }
  return e;
}
VectorTileLayer$1.prototype.feature = function(t) {
  if (t < 0 || t >= this._features.length)
    throw new Error("feature index out of bounds");
  this._pbf.pos = this._features[t];
  var e = this._pbf.readVarint() + this._pbf.pos;
  return new VectorTileFeature(this._pbf, e, this.extent, this._keys, this._values);
};
var VectorTileLayer = vectortilelayer, vectortile = VectorTile$1;
function VectorTile$1(t, e) {
  this.layers = t.readFields(readTile, {}, e);
}
function readTile(t, e, r) {
  if (3 === t) {
    var i = new VectorTileLayer(r, r.readVarint() + r.pos);
    i.length && (e[i.name] = i);
  }
}
var VectorTile = vectortile, ieee754$1 = {
  read: function(t, e, r, i, s) {
    var n, a, o = 8 * s - i - 1, h = (1 << o) - 1, l = h >> 1, c = -7, u = r ? s - 1 : 0, d = r ? -1 : 1, p = t[e + u];
    for (u += d, n = p & (1 << -c) - 1, p >>= -c, c += o; c > 0; n = 256 * n + t[e + u], u += d, c -= 8)
      ;
    for (a = n & (1 << -c) - 1, n >>= -c, c += i; c > 0; a = 256 * a + t[e + u], u += d, c -= 8)
      ;
    if (0 === n)
      n = 1 - l;
    else {
      if (n === h)
        return a ? NaN : 1 / 0 * (p ? -1 : 1);
      a += Math.pow(2, i), n -= l;
    }
    return (p ? -1 : 1) * a * Math.pow(2, n - i);
  },
  write: function(t, e, r, i, s, n) {
    var a, o, h, l = 8 * n - s - 1, c = (1 << l) - 1, u = c >> 1, d = 23 === s ? Math.pow(2, -24) - Math.pow(2, -77) : 0, p = i ? 0 : n - 1, f = i ? 1 : -1, _ = e < 0 || 0 === e && 1 / e < 0 ? 1 : 0;
    for (e = Math.abs(e), isNaN(e) || e === 1 / 0 ? (o = isNaN(e) ? 1 : 0, a = c) : (a = Math.floor(Math.log(e) / Math.LN2), e * (h = Math.pow(2, -a)) < 1 && (a--, h *= 2), (e += a + u >= 1 ? d / h : d * Math.pow(2, 1 - u)) * h >= 2 && (a++, h /= 2), a + u >= c ? (o = 0, a = c) : a + u >= 1 ? (o = (e * h - 1) * Math.pow(2, s), a += u) : (o = e * Math.pow(2, u - 1) * Math.pow(2, s), a = 0)); s >= 8; t[r + p] = 255 & o, p += f, o /= 256, s -= 8)
      ;
    for (a = a << s | o, l += s; l > 0; t[r + p] = 255 & a, p += f, a /= 256, l -= 8)
      ;
    t[r + p - f] |= 128 * _;
  }
}, pbf = Pbf, ieee754 = ieee754$1;
function Pbf(t) {
  this.buf = ArrayBuffer.isView && ArrayBuffer.isView(t) ? t : new Uint8Array(t || 0), this.pos = 0, this.type = 0, this.length = this.buf.length;
}
Pbf.Varint = 0, Pbf.Fixed64 = 1, Pbf.Bytes = 2, Pbf.Fixed32 = 5;
var SHIFT_LEFT_32 = 4294967296, SHIFT_RIGHT_32 = 1 / SHIFT_LEFT_32, TEXT_DECODER_MIN_LENGTH = 12, utf8TextDecoder = "undefined" == typeof TextDecoder ? null : new TextDecoder("utf8");
function readVarintRemainder(t, e, r) {
  var i, s, n = r.buf;
  if (i = (112 & (s = n[r.pos++])) >> 4, s < 128)
    return toNum(t, i, e);
  if (i |= (127 & (s = n[r.pos++])) << 3, s < 128)
    return toNum(t, i, e);
  if (i |= (127 & (s = n[r.pos++])) << 10, s < 128)
    return toNum(t, i, e);
  if (i |= (127 & (s = n[r.pos++])) << 17, s < 128)
    return toNum(t, i, e);
  if (i |= (127 & (s = n[r.pos++])) << 24, s < 128)
    return toNum(t, i, e);
  if (i |= (1 & (s = n[r.pos++])) << 31, s < 128)
    return toNum(t, i, e);
  throw new Error("Expected varint not more than 10 bytes");
}
function readPackedEnd(t) {
  return t.type === Pbf.Bytes ? t.readVarint() + t.pos : t.pos + 1;
}
function toNum(t, e, r) {
  return r ? 4294967296 * e + (t >>> 0) : 4294967296 * (e >>> 0) + (t >>> 0);
}
function writeBigVarint(t, e) {
  var r, i;
  if (t >= 0 ? (r = t % 4294967296 | 0, i = t / 4294967296 | 0) : (i = ~(-t / 4294967296), 4294967295 ^ (r = ~(-t % 4294967296)) ? r = r + 1 | 0 : (r = 0, i = i + 1 | 0)), t >= 18446744073709552e3 || t < -18446744073709552e3)
    throw new Error("Given varint doesn't fit into 10 bytes");
  e.realloc(10), writeBigVarintLow(r, i, e), writeBigVarintHigh(i, e);
}
function writeBigVarintLow(t, e, r) {
  r.buf[r.pos++] = 127 & t | 128, t >>>= 7, r.buf[r.pos++] = 127 & t | 128, t >>>= 7, r.buf[r.pos++] = 127 & t | 128, t >>>= 7, r.buf[r.pos++] = 127 & t | 128, t >>>= 7, r.buf[r.pos] = 127 & t;
}
function writeBigVarintHigh(t, e) {
  var r = (7 & t) << 4;
  e.buf[e.pos++] |= r | ((t >>>= 3) ? 128 : 0), t && (e.buf[e.pos++] = 127 & t | ((t >>>= 7) ? 128 : 0), t && (e.buf[e.pos++] = 127 & t | ((t >>>= 7) ? 128 : 0), t && (e.buf[e.pos++] = 127 & t | ((t >>>= 7) ? 128 : 0), t && (e.buf[e.pos++] = 127 & t | ((t >>>= 7) ? 128 : 0), t && (e.buf[e.pos++] = 127 & t)))));
}
function makeRoomForExtraLength(t, e, r) {
  var i = e <= 16383 ? 1 : e <= 2097151 ? 2 : e <= 268435455 ? 3 : Math.floor(Math.log(e) / (7 * Math.LN2));
  r.realloc(i);
  for (var s = r.pos - 1; s >= t; s--)
    r.buf[s + i] = r.buf[s];
}
function writePackedVarint(t, e) {
  for (var r = 0; r < t.length; r++)
    e.writeVarint(t[r]);
}
function writePackedSVarint(t, e) {
  for (var r = 0; r < t.length; r++)
    e.writeSVarint(t[r]);
}
function writePackedFloat(t, e) {
  for (var r = 0; r < t.length; r++)
    e.writeFloat(t[r]);
}
function writePackedDouble(t, e) {
  for (var r = 0; r < t.length; r++)
    e.writeDouble(t[r]);
}
function writePackedBoolean(t, e) {
  for (var r = 0; r < t.length; r++)
    e.writeBoolean(t[r]);
}
function writePackedFixed32(t, e) {
  for (var r = 0; r < t.length; r++)
    e.writeFixed32(t[r]);
}
function writePackedSFixed32(t, e) {
  for (var r = 0; r < t.length; r++)
    e.writeSFixed32(t[r]);
}
function writePackedFixed64(t, e) {
  for (var r = 0; r < t.length; r++)
    e.writeFixed64(t[r]);
}
function writePackedSFixed64(t, e) {
  for (var r = 0; r < t.length; r++)
    e.writeSFixed64(t[r]);
}
function readUInt32(t, e) {
  return (t[e] | t[e + 1] << 8 | t[e + 2] << 16) + 16777216 * t[e + 3];
}
function writeInt32(t, e, r) {
  t[r] = e, t[r + 1] = e >>> 8, t[r + 2] = e >>> 16, t[r + 3] = e >>> 24;
}
function readInt32(t, e) {
  return (t[e] | t[e + 1] << 8 | t[e + 2] << 16) + (t[e + 3] << 24);
}
function readUtf8(t, e, r) {
  for (var i = "", s = e; s < r; ) {
    var n, a, o, h = t[s], l = null, c = h > 239 ? 4 : h > 223 ? 3 : h > 191 ? 2 : 1;
    if (s + c > r)
      break;
    1 === c ? h < 128 && (l = h) : 2 === c ? 128 == (192 & (n = t[s + 1])) && (l = (31 & h) << 6 | 63 & n) <= 127 && (l = null) : 3 === c ? (n = t[s + 1], a = t[s + 2], 128 == (192 & n) && 128 == (192 & a) && ((l = (15 & h) << 12 | (63 & n) << 6 | 63 & a) <= 2047 || l >= 55296 && l <= 57343) && (l = null)) : 4 === c && (n = t[s + 1], a = t[s + 2], o = t[s + 3], 128 == (192 & n) && 128 == (192 & a) && 128 == (192 & o) && ((l = (15 & h) << 18 | (63 & n) << 12 | (63 & a) << 6 | 63 & o) <= 65535 || l >= 1114112) && (l = null)), null === l ? (l = 65533, c = 1) : l > 65535 && (l -= 65536, i += String.fromCharCode(l >>> 10 & 1023 | 55296), l = 56320 | 1023 & l), i += String.fromCharCode(l), s += c;
  }
  return i;
}
function readUtf8TextDecoder(t, e, r) {
  return utf8TextDecoder.decode(t.subarray(e, r));
}
function writeUtf8(t, e, r) {
  for (var i, s, n = 0; n < e.length; n++) {
    if ((i = e.charCodeAt(n)) > 55295 && i < 57344) {
      if (!s) {
        i > 56319 || n + 1 === e.length ? (t[r++] = 239, t[r++] = 191, t[r++] = 189) : s = i;
        continue;
      }
      if (i < 56320) {
        t[r++] = 239, t[r++] = 191, t[r++] = 189, s = i;
        continue;
      }
      i = s - 55296 << 10 | i - 56320 | 65536, s = null;
    } else
      s && (t[r++] = 239, t[r++] = 191, t[r++] = 189, s = null);
    i < 128 ? t[r++] = i : (i < 2048 ? t[r++] = i >> 6 | 192 : (i < 65536 ? t[r++] = i >> 12 | 224 : (t[r++] = i >> 18 | 240, t[r++] = i >> 12 & 63 | 128), t[r++] = i >> 6 & 63 | 128), t[r++] = 63 & i | 128);
  }
  return r;
}
function reFetch(t = "", e = {}, r = 3) {
  return function t4(e2, i) {
    return r--, fetch(e2, i).then((t5) => {
      if (200 === t5.status)
        return t5;
      if (404 === t5.status)
        throw new Error("404");
    }).catch((s) => {
      if (i.signal && "AbortError" === s.name)
        throw s;
      if (r > 0)
        return t4(e2, i);
      throw s;
    });
  }(t, e);
}
/**
 * @license
 * Copyright 2010-2025 Three.js Authors
 * SPDX-License-Identifier: MIT
 */
Pbf.prototype = { destroy: function() {
  this.buf = null;
}, readFields: function(t, e, r) {
  for (r = r || this.length; this.pos < r; ) {
    var i = this.readVarint(), s = i >> 3, n = this.pos;
    this.type = 7 & i, t(s, e, this), this.pos === n && this.skip(i);
  }
  return e;
}, readMessage: function(t, e) {
  return this.readFields(t, e, this.readVarint() + this.pos);
}, readFixed32: function() {
  var t = readUInt32(this.buf, this.pos);
  return this.pos += 4, t;
}, readSFixed32: function() {
  var t = readInt32(this.buf, this.pos);
  return this.pos += 4, t;
}, readFixed64: function() {
  var t = readUInt32(this.buf, this.pos) + readUInt32(this.buf, this.pos + 4) * SHIFT_LEFT_32;
  return this.pos += 8, t;
}, readSFixed64: function() {
  var t = readUInt32(this.buf, this.pos) + readInt32(this.buf, this.pos + 4) * SHIFT_LEFT_32;
  return this.pos += 8, t;
}, readFloat: function() {
  var t = ieee754.read(this.buf, this.pos, true, 23, 4);
  return this.pos += 4, t;
}, readDouble: function() {
  var t = ieee754.read(this.buf, this.pos, true, 52, 8);
  return this.pos += 8, t;
}, readVarint: function(t) {
  var e, r, i = this.buf;
  return e = 127 & (r = i[this.pos++]), r < 128 ? e : (e |= (127 & (r = i[this.pos++])) << 7, r < 128 ? e : (e |= (127 & (r = i[this.pos++])) << 14, r < 128 ? e : (e |= (127 & (r = i[this.pos++])) << 21, r < 128 ? e : readVarintRemainder(e |= (15 & (r = i[this.pos])) << 28, t, this))));
}, readVarint64: function() {
  return this.readVarint(true);
}, readSVarint: function() {
  var t = this.readVarint();
  return t % 2 == 1 ? (t + 1) / -2 : t / 2;
}, readBoolean: function() {
  return Boolean(this.readVarint());
}, readString: function() {
  var t = this.readVarint() + this.pos, e = this.pos;
  return this.pos = t, t - e >= TEXT_DECODER_MIN_LENGTH && utf8TextDecoder ? readUtf8TextDecoder(this.buf, e, t) : readUtf8(this.buf, e, t);
}, readBytes: function() {
  var t = this.readVarint() + this.pos, e = this.buf.subarray(this.pos, t);
  return this.pos = t, e;
}, readPackedVarint: function(t, e) {
  if (this.type !== Pbf.Bytes)
    return t.push(this.readVarint(e));
  var r = readPackedEnd(this);
  for (t = t || []; this.pos < r; )
    t.push(this.readVarint(e));
  return t;
}, readPackedSVarint: function(t) {
  if (this.type !== Pbf.Bytes)
    return t.push(this.readSVarint());
  var e = readPackedEnd(this);
  for (t = t || []; this.pos < e; )
    t.push(this.readSVarint());
  return t;
}, readPackedBoolean: function(t) {
  if (this.type !== Pbf.Bytes)
    return t.push(this.readBoolean());
  var e = readPackedEnd(this);
  for (t = t || []; this.pos < e; )
    t.push(this.readBoolean());
  return t;
}, readPackedFloat: function(t) {
  if (this.type !== Pbf.Bytes)
    return t.push(this.readFloat());
  var e = readPackedEnd(this);
  for (t = t || []; this.pos < e; )
    t.push(this.readFloat());
  return t;
}, readPackedDouble: function(t) {
  if (this.type !== Pbf.Bytes)
    return t.push(this.readDouble());
  var e = readPackedEnd(this);
  for (t = t || []; this.pos < e; )
    t.push(this.readDouble());
  return t;
}, readPackedFixed32: function(t) {
  if (this.type !== Pbf.Bytes)
    return t.push(this.readFixed32());
  var e = readPackedEnd(this);
  for (t = t || []; this.pos < e; )
    t.push(this.readFixed32());
  return t;
}, readPackedSFixed32: function(t) {
  if (this.type !== Pbf.Bytes)
    return t.push(this.readSFixed32());
  var e = readPackedEnd(this);
  for (t = t || []; this.pos < e; )
    t.push(this.readSFixed32());
  return t;
}, readPackedFixed64: function(t) {
  if (this.type !== Pbf.Bytes)
    return t.push(this.readFixed64());
  var e = readPackedEnd(this);
  for (t = t || []; this.pos < e; )
    t.push(this.readFixed64());
  return t;
}, readPackedSFixed64: function(t) {
  if (this.type !== Pbf.Bytes)
    return t.push(this.readSFixed64());
  var e = readPackedEnd(this);
  for (t = t || []; this.pos < e; )
    t.push(this.readSFixed64());
  return t;
}, skip: function(t) {
  var e = 7 & t;
  if (e === Pbf.Varint)
    for (; this.buf[this.pos++] > 127; )
      ;
  else if (e === Pbf.Bytes)
    this.pos = this.readVarint() + this.pos;
  else if (e === Pbf.Fixed32)
    this.pos += 4;
  else {
    if (e !== Pbf.Fixed64)
      throw new Error("Unimplemented type: " + e);
    this.pos += 8;
  }
}, writeTag: function(t, e) {
  this.writeVarint(t << 3 | e);
}, realloc: function(t) {
  for (var e = this.length || 16; e < this.pos + t; )
    e *= 2;
  if (e !== this.length) {
    var r = new Uint8Array(e);
    r.set(this.buf), this.buf = r, this.length = e;
  }
}, finish: function() {
  return this.length = this.pos, this.pos = 0, this.buf.subarray(0, this.length);
}, writeFixed32: function(t) {
  this.realloc(4), writeInt32(this.buf, t, this.pos), this.pos += 4;
}, writeSFixed32: function(t) {
  this.realloc(4), writeInt32(this.buf, t, this.pos), this.pos += 4;
}, writeFixed64: function(t) {
  this.realloc(8), writeInt32(this.buf, -1 & t, this.pos), writeInt32(this.buf, Math.floor(t * SHIFT_RIGHT_32), this.pos + 4), this.pos += 8;
}, writeSFixed64: function(t) {
  this.realloc(8), writeInt32(this.buf, -1 & t, this.pos), writeInt32(this.buf, Math.floor(t * SHIFT_RIGHT_32), this.pos + 4), this.pos += 8;
}, writeVarint: function(t) {
  (t = +t || 0) > 268435455 || t < 0 ? writeBigVarint(t, this) : (this.realloc(4), this.buf[this.pos++] = 127 & t | (t > 127 ? 128 : 0), t <= 127 || (this.buf[this.pos++] = 127 & (t >>>= 7) | (t > 127 ? 128 : 0), t <= 127 || (this.buf[this.pos++] = 127 & (t >>>= 7) | (t > 127 ? 128 : 0), t <= 127 || (this.buf[this.pos++] = t >>> 7 & 127))));
}, writeSVarint: function(t) {
  this.writeVarint(t < 0 ? 2 * -t - 1 : 2 * t);
}, writeBoolean: function(t) {
  this.writeVarint(Boolean(t));
}, writeString: function(t) {
  t = String(t), this.realloc(4 * t.length), this.pos++;
  var e = this.pos;
  this.pos = writeUtf8(this.buf, t, this.pos);
  var r = this.pos - e;
  r >= 128 && makeRoomForExtraLength(e, r, this), this.pos = e - 1, this.writeVarint(r), this.pos += r;
}, writeFloat: function(t) {
  this.realloc(4), ieee754.write(this.buf, t, this.pos, true, 23, 4), this.pos += 4;
}, writeDouble: function(t) {
  this.realloc(8), ieee754.write(this.buf, t, this.pos, true, 52, 8), this.pos += 8;
}, writeBytes: function(t) {
  var e = t.length;
  this.writeVarint(e), this.realloc(e);
  for (var r = 0; r < e; r++)
    this.buf[this.pos++] = t[r];
}, writeRawMessage: function(t, e) {
  this.pos++;
  var r = this.pos;
  t(e, this);
  var i = this.pos - r;
  i >= 128 && makeRoomForExtraLength(r, i, this), this.pos = r - 1, this.writeVarint(i), this.pos += i;
}, writeMessage: function(t, e, r) {
  this.writeTag(t, Pbf.Bytes), this.writeRawMessage(e, r);
}, writePackedVarint: function(t, e) {
  e.length && this.writeMessage(t, writePackedVarint, e);
}, writePackedSVarint: function(t, e) {
  e.length && this.writeMessage(t, writePackedSVarint, e);
}, writePackedBoolean: function(t, e) {
  e.length && this.writeMessage(t, writePackedBoolean, e);
}, writePackedFloat: function(t, e) {
  e.length && this.writeMessage(t, writePackedFloat, e);
}, writePackedDouble: function(t, e) {
  e.length && this.writeMessage(t, writePackedDouble, e);
}, writePackedFixed32: function(t, e) {
  e.length && this.writeMessage(t, writePackedFixed32, e);
}, writePackedSFixed32: function(t, e) {
  e.length && this.writeMessage(t, writePackedSFixed32, e);
}, writePackedFixed64: function(t, e) {
  e.length && this.writeMessage(t, writePackedFixed64, e);
}, writePackedSFixed64: function(t, e) {
  e.length && this.writeMessage(t, writePackedSFixed64, e);
}, writeBytesField: function(t, e) {
  this.writeTag(t, Pbf.Bytes), this.writeBytes(e);
}, writeFixed32Field: function(t, e) {
  this.writeTag(t, Pbf.Fixed32), this.writeFixed32(e);
}, writeSFixed32Field: function(t, e) {
  this.writeTag(t, Pbf.Fixed32), this.writeSFixed32(e);
}, writeFixed64Field: function(t, e) {
  this.writeTag(t, Pbf.Fixed64), this.writeFixed64(e);
}, writeSFixed64Field: function(t, e) {
  this.writeTag(t, Pbf.Fixed64), this.writeSFixed64(e);
}, writeVarintField: function(t, e) {
  this.writeTag(t, Pbf.Varint), this.writeVarint(e);
}, writeSVarintField: function(t, e) {
  this.writeTag(t, Pbf.Varint), this.writeSVarint(e);
}, writeStringField: function(t, e) {
  this.writeTag(t, Pbf.Bytes), this.writeString(e);
}, writeFloatField: function(t, e) {
  this.writeTag(t, Pbf.Fixed32), this.writeFloat(e);
}, writeDoubleField: function(t, e) {
  this.writeTag(t, Pbf.Fixed64), this.writeDouble(e);
}, writeBooleanField: function(t, e) {
  this.writeVarintField(t, Boolean(e));
} };
const REVISION = "179", NoColorSpace = "", SRGBColorSpace = "srgb", LinearSRGBColorSpace = "srgb-linear", LinearTransfer = "linear", SRGBTransfer = "srgb", WebGLCoordinateSystem = 2e3, WebGPUCoordinateSystem = 2001, _lut = ["00", "01", "02", "03", "04", "05", "06", "07", "08", "09", "0a", "0b", "0c", "0d", "0e", "0f", "10", "11", "12", "13", "14", "15", "16", "17", "18", "19", "1a", "1b", "1c", "1d", "1e", "1f", "20", "21", "22", "23", "24", "25", "26", "27", "28", "29", "2a", "2b", "2c", "2d", "2e", "2f", "30", "31", "32", "33", "34", "35", "36", "37", "38", "39", "3a", "3b", "3c", "3d", "3e", "3f", "40", "41", "42", "43", "44", "45", "46", "47", "48", "49", "4a", "4b", "4c", "4d", "4e", "4f", "50", "51", "52", "53", "54", "55", "56", "57", "58", "59", "5a", "5b", "5c", "5d", "5e", "5f", "60", "61", "62", "63", "64", "65", "66", "67", "68", "69", "6a", "6b", "6c", "6d", "6e", "6f", "70", "71", "72", "73", "74", "75", "76", "77", "78", "79", "7a", "7b", "7c", "7d", "7e", "7f", "80", "81", "82", "83", "84", "85", "86", "87", "88", "89", "8a", "8b", "8c", "8d", "8e", "8f", "90", "91", "92", "93", "94", "95", "96", "97", "98", "99", "9a", "9b", "9c", "9d", "9e", "9f", "a0", "a1", "a2", "a3", "a4", "a5", "a6", "a7", "a8", "a9", "aa", "ab", "ac", "ad", "ae", "af", "b0", "b1", "b2", "b3", "b4", "b5", "b6", "b7", "b8", "b9", "ba", "bb", "bc", "bd", "be", "bf", "c0", "c1", "c2", "c3", "c4", "c5", "c6", "c7", "c8", "c9", "ca", "cb", "cc", "cd", "ce", "cf", "d0", "d1", "d2", "d3", "d4", "d5", "d6", "d7", "d8", "d9", "da", "db", "dc", "dd", "de", "df", "e0", "e1", "e2", "e3", "e4", "e5", "e6", "e7", "e8", "e9", "ea", "eb", "ec", "ed", "ee", "ef", "f0", "f1", "f2", "f3", "f4", "f5", "f6", "f7", "f8", "f9", "fa", "fb", "fc", "fd", "fe", "ff"];
let _seed = 1234567;
const DEG2RAD = Math.PI / 180, RAD2DEG = 180 / Math.PI;
function generateUUID() {
  const t = 4294967295 * Math.random() | 0, e = 4294967295 * Math.random() | 0, r = 4294967295 * Math.random() | 0, i = 4294967295 * Math.random() | 0;
  return (_lut[255 & t] + _lut[t >> 8 & 255] + _lut[t >> 16 & 255] + _lut[t >> 24 & 255] + "-" + _lut[255 & e] + _lut[e >> 8 & 255] + "-" + _lut[e >> 16 & 15 | 64] + _lut[e >> 24 & 255] + "-" + _lut[63 & r | 128] + _lut[r >> 8 & 255] + "-" + _lut[r >> 16 & 255] + _lut[r >> 24 & 255] + _lut[255 & i] + _lut[i >> 8 & 255] + _lut[i >> 16 & 255] + _lut[i >> 24 & 255]).toLowerCase();
}
function clamp$1(t, e, r) {
  return Math.max(e, Math.min(r, t));
}
function euclideanModulo(t, e) {
  return (t % e + e) % e;
}
function mapLinear(t, e, r, i, s) {
  return i + (t - e) * (s - i) / (r - e);
}
function inverseLerp(t, e, r) {
  return t !== e ? (r - t) / (e - t) : 0;
}
function lerp(t, e, r) {
  return (1 - r) * t + r * e;
}
function damp(t, e, r, i) {
  return lerp(t, e, 1 - Math.exp(-r * i));
}
function pingpong(t, e = 1) {
  return e - Math.abs(euclideanModulo(t, 2 * e) - e);
}
function smoothstep(t, e, r) {
  return t <= e ? 0 : t >= r ? 1 : (t = (t - e) / (r - e)) * t * (3 - 2 * t);
}
function smootherstep(t, e, r) {
  return t <= e ? 0 : t >= r ? 1 : (t = (t - e) / (r - e)) * t * t * (t * (6 * t - 15) + 10);
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
function setQuaternionFromProperEuler(t, e, r, i, s) {
  const n = Math.cos, a = Math.sin, o = n(r / 2), h = a(r / 2), l = n((e + i) / 2), c = a((e + i) / 2), u = n((e - i) / 2), d = a((e - i) / 2), p = n((i - e) / 2), f = a((i - e) / 2);
  switch (s) {
    case "XYX":
      t.set(o * c, h * u, h * d, o * l);
      break;
    case "YZY":
      t.set(h * d, o * c, h * u, o * l);
      break;
    case "ZXZ":
      t.set(h * u, h * d, o * c, o * l);
      break;
    case "XZX":
      t.set(o * c, h * f, h * p, o * l);
      break;
    case "YXY":
      t.set(h * p, o * c, h * f, o * l);
      break;
    case "ZYZ":
      t.set(h * f, h * p, o * c, o * l);
      break;
    default:
      console.warn("THREE.MathUtils: .setQuaternionFromProperEuler() encountered an unknown order: " + s);
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
    const e = this.x, r = this.y, i = t.elements;
    return this.x = i[0] * e + i[3] * r + i[6], this.y = i[1] * e + i[4] * r + i[7], this;
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
    const r = this.length();
    return this.divideScalar(r || 1).multiplyScalar(clamp$1(r, t, e));
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
    const r = this.dot(t) / e;
    return Math.acos(clamp$1(r, -1, 1));
  }
  distanceTo(t) {
    return Math.sqrt(this.distanceToSquared(t));
  }
  distanceToSquared(t) {
    const e = this.x - t.x, r = this.y - t.y;
    return e * e + r * r;
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
  lerpVectors(t, e, r) {
    return this.x = t.x + (e.x - t.x) * r, this.y = t.y + (e.y - t.y) * r, this;
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
    const r = Math.cos(e), i = Math.sin(e), s = this.x - t.x, n = this.y - t.y;
    return this.x = s * r - n * i + t.x, this.y = s * i + n * r + t.y, this;
  }
  random() {
    return this.x = Math.random(), this.y = Math.random(), this;
  }
  *[Symbol.iterator]() {
    yield this.x, yield this.y;
  }
}
class Quaternion$1 {
  constructor(t = 0, e = 0, r = 0, i = 1) {
    this.isQuaternion = true, this._x = t, this._y = e, this._z = r, this._w = i;
  }
  static slerpFlat(t, e, r, i, s, n, a) {
    let o = r[i + 0], h = r[i + 1], l = r[i + 2], c = r[i + 3];
    const u = s[n + 0], d = s[n + 1], p = s[n + 2], f = s[n + 3];
    if (0 === a)
      return t[e + 0] = o, t[e + 1] = h, t[e + 2] = l, void (t[e + 3] = c);
    if (1 === a)
      return t[e + 0] = u, t[e + 1] = d, t[e + 2] = p, void (t[e + 3] = f);
    if (c !== f || o !== u || h !== d || l !== p) {
      let t4 = 1 - a;
      const e2 = o * u + h * d + l * p + c * f, r2 = e2 >= 0 ? 1 : -1, i2 = 1 - e2 * e2;
      if (i2 > Number.EPSILON) {
        const s3 = Math.sqrt(i2), n2 = Math.atan2(s3, e2 * r2);
        t4 = Math.sin(t4 * n2) / s3, a = Math.sin(a * n2) / s3;
      }
      const s2 = a * r2;
      if (o = o * t4 + u * s2, h = h * t4 + d * s2, l = l * t4 + p * s2, c = c * t4 + f * s2, t4 === 1 - a) {
        const t5 = 1 / Math.sqrt(o * o + h * h + l * l + c * c);
        o *= t5, h *= t5, l *= t5, c *= t5;
      }
    }
    t[e] = o, t[e + 1] = h, t[e + 2] = l, t[e + 3] = c;
  }
  static multiplyQuaternionsFlat(t, e, r, i, s, n) {
    const a = r[i], o = r[i + 1], h = r[i + 2], l = r[i + 3], c = s[n], u = s[n + 1], d = s[n + 2], p = s[n + 3];
    return t[e] = a * p + l * c + o * d - h * u, t[e + 1] = o * p + l * u + h * c - a * d, t[e + 2] = h * p + l * d + a * u - o * c, t[e + 3] = l * p - a * c - o * u - h * d, t;
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
  set(t, e, r, i) {
    return this._x = t, this._y = e, this._z = r, this._w = i, this._onChangeCallback(), this;
  }
  clone() {
    return new this.constructor(this._x, this._y, this._z, this._w);
  }
  copy(t) {
    return this._x = t.x, this._y = t.y, this._z = t.z, this._w = t.w, this._onChangeCallback(), this;
  }
  setFromEuler(t, e = true) {
    const r = t._x, i = t._y, s = t._z, n = t._order, a = Math.cos, o = Math.sin, h = a(r / 2), l = a(i / 2), c = a(s / 2), u = o(r / 2), d = o(i / 2), p = o(s / 2);
    switch (n) {
      case "XYZ":
        this._x = u * l * c + h * d * p, this._y = h * d * c - u * l * p, this._z = h * l * p + u * d * c, this._w = h * l * c - u * d * p;
        break;
      case "YXZ":
        this._x = u * l * c + h * d * p, this._y = h * d * c - u * l * p, this._z = h * l * p - u * d * c, this._w = h * l * c + u * d * p;
        break;
      case "ZXY":
        this._x = u * l * c - h * d * p, this._y = h * d * c + u * l * p, this._z = h * l * p + u * d * c, this._w = h * l * c - u * d * p;
        break;
      case "ZYX":
        this._x = u * l * c - h * d * p, this._y = h * d * c + u * l * p, this._z = h * l * p - u * d * c, this._w = h * l * c + u * d * p;
        break;
      case "YZX":
        this._x = u * l * c + h * d * p, this._y = h * d * c + u * l * p, this._z = h * l * p - u * d * c, this._w = h * l * c - u * d * p;
        break;
      case "XZY":
        this._x = u * l * c - h * d * p, this._y = h * d * c - u * l * p, this._z = h * l * p + u * d * c, this._w = h * l * c + u * d * p;
        break;
      default:
        console.warn("THREE.Quaternion: .setFromEuler() encountered an unknown order: " + n);
    }
    return true === e && this._onChangeCallback(), this;
  }
  setFromAxisAngle(t, e) {
    const r = e / 2, i = Math.sin(r);
    return this._x = t.x * i, this._y = t.y * i, this._z = t.z * i, this._w = Math.cos(r), this._onChangeCallback(), this;
  }
  setFromRotationMatrix(t) {
    const e = t.elements, r = e[0], i = e[4], s = e[8], n = e[1], a = e[5], o = e[9], h = e[2], l = e[6], c = e[10], u = r + a + c;
    if (u > 0) {
      const t4 = 0.5 / Math.sqrt(u + 1);
      this._w = 0.25 / t4, this._x = (l - o) * t4, this._y = (s - h) * t4, this._z = (n - i) * t4;
    } else if (r > a && r > c) {
      const t4 = 2 * Math.sqrt(1 + r - a - c);
      this._w = (l - o) / t4, this._x = 0.25 * t4, this._y = (i + n) / t4, this._z = (s + h) / t4;
    } else if (a > c) {
      const t4 = 2 * Math.sqrt(1 + a - r - c);
      this._w = (s - h) / t4, this._x = (i + n) / t4, this._y = 0.25 * t4, this._z = (o + l) / t4;
    } else {
      const t4 = 2 * Math.sqrt(1 + c - r - a);
      this._w = (n - i) / t4, this._x = (s + h) / t4, this._y = (o + l) / t4, this._z = 0.25 * t4;
    }
    return this._onChangeCallback(), this;
  }
  setFromUnitVectors(t, e) {
    let r = t.dot(e) + 1;
    return r < 1e-8 ? (r = 0, Math.abs(t.x) > Math.abs(t.z) ? (this._x = -t.y, this._y = t.x, this._z = 0, this._w = r) : (this._x = 0, this._y = -t.z, this._z = t.y, this._w = r)) : (this._x = t.y * e.z - t.z * e.y, this._y = t.z * e.x - t.x * e.z, this._z = t.x * e.y - t.y * e.x, this._w = r), this.normalize();
  }
  angleTo(t) {
    return 2 * Math.acos(Math.abs(clamp$1(this.dot(t), -1, 1)));
  }
  rotateTowards(t, e) {
    const r = this.angleTo(t);
    if (0 === r)
      return this;
    const i = Math.min(1, e / r);
    return this.slerp(t, i), this;
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
    const r = t._x, i = t._y, s = t._z, n = t._w, a = e._x, o = e._y, h = e._z, l = e._w;
    return this._x = r * l + n * a + i * h - s * o, this._y = i * l + n * o + s * a - r * h, this._z = s * l + n * h + r * o - i * a, this._w = n * l - r * a - i * o - s * h, this._onChangeCallback(), this;
  }
  slerp(t, e) {
    if (0 === e)
      return this;
    if (1 === e)
      return this.copy(t);
    const r = this._x, i = this._y, s = this._z, n = this._w;
    let a = n * t._w + r * t._x + i * t._y + s * t._z;
    if (a < 0 ? (this._w = -t._w, this._x = -t._x, this._y = -t._y, this._z = -t._z, a = -a) : this.copy(t), a >= 1)
      return this._w = n, this._x = r, this._y = i, this._z = s, this;
    const o = 1 - a * a;
    if (o <= Number.EPSILON) {
      const t4 = 1 - e;
      return this._w = t4 * n + e * this._w, this._x = t4 * r + e * this._x, this._y = t4 * i + e * this._y, this._z = t4 * s + e * this._z, this.normalize(), this;
    }
    const h = Math.sqrt(o), l = Math.atan2(h, a), c = Math.sin((1 - e) * l) / h, u = Math.sin(e * l) / h;
    return this._w = n * c + this._w * u, this._x = r * c + this._x * u, this._y = i * c + this._y * u, this._z = s * c + this._z * u, this._onChangeCallback(), this;
  }
  slerpQuaternions(t, e, r) {
    return this.copy(t).slerp(e, r);
  }
  random() {
    const t = 2 * Math.PI * Math.random(), e = 2 * Math.PI * Math.random(), r = Math.random(), i = Math.sqrt(1 - r), s = Math.sqrt(r);
    return this.set(i * Math.sin(t), i * Math.cos(t), s * Math.sin(e), s * Math.cos(e));
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
  constructor(t = 0, e = 0, r = 0) {
    Vector3$1.prototype.isVector3 = true, this.x = t, this.y = e, this.z = r;
  }
  set(t, e, r) {
    return void 0 === r && (r = this.z), this.x = t, this.y = e, this.z = r, this;
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
    const e = this.x, r = this.y, i = this.z, s = t.elements;
    return this.x = s[0] * e + s[3] * r + s[6] * i, this.y = s[1] * e + s[4] * r + s[7] * i, this.z = s[2] * e + s[5] * r + s[8] * i, this;
  }
  applyNormalMatrix(t) {
    return this.applyMatrix3(t).normalize();
  }
  applyMatrix4(t) {
    const e = this.x, r = this.y, i = this.z, s = t.elements, n = 1 / (s[3] * e + s[7] * r + s[11] * i + s[15]);
    return this.x = (s[0] * e + s[4] * r + s[8] * i + s[12]) * n, this.y = (s[1] * e + s[5] * r + s[9] * i + s[13]) * n, this.z = (s[2] * e + s[6] * r + s[10] * i + s[14]) * n, this;
  }
  applyQuaternion(t) {
    const e = this.x, r = this.y, i = this.z, s = t.x, n = t.y, a = t.z, o = t.w, h = 2 * (n * i - a * r), l = 2 * (a * e - s * i), c = 2 * (s * r - n * e);
    return this.x = e + o * h + n * c - a * l, this.y = r + o * l + a * h - s * c, this.z = i + o * c + s * l - n * h, this;
  }
  project(t) {
    return this.applyMatrix4(t.matrixWorldInverse).applyMatrix4(t.projectionMatrix);
  }
  unproject(t) {
    return this.applyMatrix4(t.projectionMatrixInverse).applyMatrix4(t.matrixWorld);
  }
  transformDirection(t) {
    const e = this.x, r = this.y, i = this.z, s = t.elements;
    return this.x = s[0] * e + s[4] * r + s[8] * i, this.y = s[1] * e + s[5] * r + s[9] * i, this.z = s[2] * e + s[6] * r + s[10] * i, this.normalize();
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
    const r = this.length();
    return this.divideScalar(r || 1).multiplyScalar(clamp$1(r, t, e));
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
  lerpVectors(t, e, r) {
    return this.x = t.x + (e.x - t.x) * r, this.y = t.y + (e.y - t.y) * r, this.z = t.z + (e.z - t.z) * r, this;
  }
  cross(t) {
    return this.crossVectors(this, t);
  }
  crossVectors(t, e) {
    const r = t.x, i = t.y, s = t.z, n = e.x, a = e.y, o = e.z;
    return this.x = i * o - s * a, this.y = s * n - r * o, this.z = r * a - i * n, this;
  }
  projectOnVector(t) {
    const e = t.lengthSq();
    if (0 === e)
      return this.set(0, 0, 0);
    const r = t.dot(this) / e;
    return this.copy(t).multiplyScalar(r);
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
    const r = this.dot(t) / e;
    return Math.acos(clamp$1(r, -1, 1));
  }
  distanceTo(t) {
    return Math.sqrt(this.distanceToSquared(t));
  }
  distanceToSquared(t) {
    const e = this.x - t.x, r = this.y - t.y, i = this.z - t.z;
    return e * e + r * r + i * i;
  }
  manhattanDistanceTo(t) {
    return Math.abs(this.x - t.x) + Math.abs(this.y - t.y) + Math.abs(this.z - t.z);
  }
  setFromSpherical(t) {
    return this.setFromSphericalCoords(t.radius, t.phi, t.theta);
  }
  setFromSphericalCoords(t, e, r) {
    const i = Math.sin(e) * t;
    return this.x = i * Math.sin(r), this.y = Math.cos(e) * t, this.z = i * Math.cos(r), this;
  }
  setFromCylindrical(t) {
    return this.setFromCylindricalCoords(t.radius, t.theta, t.y);
  }
  setFromCylindricalCoords(t, e, r) {
    return this.x = t * Math.sin(e), this.y = r, this.z = t * Math.cos(e), this;
  }
  setFromMatrixPosition(t) {
    const e = t.elements;
    return this.x = e[12], this.y = e[13], this.z = e[14], this;
  }
  setFromMatrixScale(t) {
    const e = this.setFromMatrixColumn(t, 0).length(), r = this.setFromMatrixColumn(t, 1).length(), i = this.setFromMatrixColumn(t, 2).length();
    return this.x = e, this.y = r, this.z = i, this;
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
    const t = Math.random() * Math.PI * 2, e = 2 * Math.random() - 1, r = Math.sqrt(1 - e * e);
    return this.x = r * Math.cos(t), this.y = e, this.z = r * Math.sin(t), this;
  }
  *[Symbol.iterator]() {
    yield this.x, yield this.y, yield this.z;
  }
}
const _vector$c = new Vector3$1(), _quaternion$4 = new Quaternion$1();
class Matrix3 {
  constructor(t, e, r, i, s, n, a, o, h) {
    Matrix3.prototype.isMatrix3 = true, this.elements = [1, 0, 0, 0, 1, 0, 0, 0, 1], void 0 !== t && this.set(t, e, r, i, s, n, a, o, h);
  }
  set(t, e, r, i, s, n, a, o, h) {
    const l = this.elements;
    return l[0] = t, l[1] = i, l[2] = a, l[3] = e, l[4] = s, l[5] = o, l[6] = r, l[7] = n, l[8] = h, this;
  }
  identity() {
    return this.set(1, 0, 0, 0, 1, 0, 0, 0, 1), this;
  }
  copy(t) {
    const e = this.elements, r = t.elements;
    return e[0] = r[0], e[1] = r[1], e[2] = r[2], e[3] = r[3], e[4] = r[4], e[5] = r[5], e[6] = r[6], e[7] = r[7], e[8] = r[8], this;
  }
  extractBasis(t, e, r) {
    return t.setFromMatrix3Column(this, 0), e.setFromMatrix3Column(this, 1), r.setFromMatrix3Column(this, 2), this;
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
    const r = t.elements, i = e.elements, s = this.elements, n = r[0], a = r[3], o = r[6], h = r[1], l = r[4], c = r[7], u = r[2], d = r[5], p = r[8], f = i[0], _ = i[3], y = i[6], m = i[1], g = i[4], x = i[7], M = i[2], w = i[5], b = i[8];
    return s[0] = n * f + a * m + o * M, s[3] = n * _ + a * g + o * w, s[6] = n * y + a * x + o * b, s[1] = h * f + l * m + c * M, s[4] = h * _ + l * g + c * w, s[7] = h * y + l * x + c * b, s[2] = u * f + d * m + p * M, s[5] = u * _ + d * g + p * w, s[8] = u * y + d * x + p * b, this;
  }
  multiplyScalar(t) {
    const e = this.elements;
    return e[0] *= t, e[3] *= t, e[6] *= t, e[1] *= t, e[4] *= t, e[7] *= t, e[2] *= t, e[5] *= t, e[8] *= t, this;
  }
  determinant() {
    const t = this.elements, e = t[0], r = t[1], i = t[2], s = t[3], n = t[4], a = t[5], o = t[6], h = t[7], l = t[8];
    return e * n * l - e * a * h - r * s * l + r * a * o + i * s * h - i * n * o;
  }
  invert() {
    const t = this.elements, e = t[0], r = t[1], i = t[2], s = t[3], n = t[4], a = t[5], o = t[6], h = t[7], l = t[8], c = l * n - a * h, u = a * o - l * s, d = h * s - n * o, p = e * c + r * u + i * d;
    if (0 === p)
      return this.set(0, 0, 0, 0, 0, 0, 0, 0, 0);
    const f = 1 / p;
    return t[0] = c * f, t[1] = (i * h - l * r) * f, t[2] = (a * r - i * n) * f, t[3] = u * f, t[4] = (l * e - i * o) * f, t[5] = (i * s - a * e) * f, t[6] = d * f, t[7] = (r * o - h * e) * f, t[8] = (n * e - r * s) * f, this;
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
  setUvTransform(t, e, r, i, s, n, a) {
    const o = Math.cos(s), h = Math.sin(s);
    return this.set(r * o, r * h, -r * (o * n + h * a) + n + t, -i * h, i * o, -i * (-h * n + o * a) + a + e, 0, 0, 1), this;
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
    const e = Math.cos(t), r = Math.sin(t);
    return this.set(e, -r, 0, r, e, 0, 0, 0, 1), this;
  }
  makeScale(t, e) {
    return this.set(t, 0, 0, 0, e, 0, 0, 0, 1), this;
  }
  equals(t) {
    const e = this.elements, r = t.elements;
    for (let t4 = 0; t4 < 9; t4++)
      if (e[t4] !== r[t4])
        return false;
    return true;
  }
  fromArray(t, e = 0) {
    for (let r = 0; r < 9; r++)
      this.elements[r] = t[r + e];
    return this;
  }
  toArray(t = [], e = 0) {
    const r = this.elements;
    return t[e] = r[0], t[e + 1] = r[1], t[e + 2] = r[2], t[e + 3] = r[3], t[e + 4] = r[4], t[e + 5] = r[5], t[e + 6] = r[6], t[e + 7] = r[7], t[e + 8] = r[8], t;
  }
  clone() {
    return new this.constructor().fromArray(this.elements);
  }
}
const _m3 = new Matrix3(), _cache$1 = {};
function warnOnce(t) {
  t in _cache$1 || (_cache$1[t] = true, console.warn(t));
}
const LINEAR_REC709_TO_XYZ = new Matrix3().set(0.4123908, 0.3575843, 0.1804808, 0.212639, 0.7151687, 0.0721923, 0.0193308, 0.1191948, 0.9505322), XYZ_TO_LINEAR_REC709 = new Matrix3().set(3.2409699, -1.5373832, -0.4986108, -0.9692436, 1.8759675, 0.0415551, 0.0556301, -0.203977, 1.0569715);
function createColorManagement() {
  const t = { enabled: true, workingColorSpace: LinearSRGBColorSpace, spaces: {}, convert: function(t4, e2, r2) {
    return false !== this.enabled && e2 !== r2 && e2 && r2 ? (this.spaces[e2].transfer === SRGBTransfer && (t4.r = SRGBToLinear(t4.r), t4.g = SRGBToLinear(t4.g), t4.b = SRGBToLinear(t4.b)), this.spaces[e2].primaries !== this.spaces[r2].primaries && (t4.applyMatrix3(this.spaces[e2].toXYZ), t4.applyMatrix3(this.spaces[r2].fromXYZ)), this.spaces[r2].transfer === SRGBTransfer && (t4.r = LinearToSRGB(t4.r), t4.g = LinearToSRGB(t4.g), t4.b = LinearToSRGB(t4.b)), t4) : t4;
  }, workingToColorSpace: function(t4, e2) {
    return this.convert(t4, this.workingColorSpace, e2);
  }, colorSpaceToWorking: function(t4, e2) {
    return this.convert(t4, e2, this.workingColorSpace);
  }, getPrimaries: function(t4) {
    return this.spaces[t4].primaries;
  }, getTransfer: function(t4) {
    return t4 === NoColorSpace ? LinearTransfer : this.spaces[t4].transfer;
  }, getLuminanceCoefficients: function(t4, e2 = this.workingColorSpace) {
    return t4.fromArray(this.spaces[e2].luminanceCoefficients);
  }, define: function(t4) {
    Object.assign(this.spaces, t4);
  }, _getMatrix: function(t4, e2, r2) {
    return t4.copy(this.spaces[e2].toXYZ).multiply(this.spaces[r2].fromXYZ);
  }, _getDrawingBufferColorSpace: function(t4) {
    return this.spaces[t4].outputColorSpaceConfig.drawingBufferColorSpace;
  }, _getUnpackColorSpace: function(t4 = this.workingColorSpace) {
    return this.spaces[t4].workingColorSpaceConfig.unpackColorSpace;
  }, fromWorkingColorSpace: function(e2, r2) {
    return warnOnce("THREE.ColorManagement: .fromWorkingColorSpace() has been renamed to .workingToColorSpace()."), t.workingToColorSpace(e2, r2);
  }, toWorkingColorSpace: function(e2, r2) {
    return warnOnce("THREE.ColorManagement: .toWorkingColorSpace() has been renamed to .colorSpaceToWorking()."), t.colorSpaceToWorking(e2, r2);
  } }, e = [0.64, 0.33, 0.3, 0.6, 0.15, 0.06], r = [0.2126, 0.7152, 0.0722], i = [0.3127, 0.329];
  return t.define({ [LinearSRGBColorSpace]: { primaries: e, whitePoint: i, transfer: LinearTransfer, toXYZ: LINEAR_REC709_TO_XYZ, fromXYZ: XYZ_TO_LINEAR_REC709, luminanceCoefficients: r, workingColorSpaceConfig: { unpackColorSpace: SRGBColorSpace }, outputColorSpaceConfig: { drawingBufferColorSpace: SRGBColorSpace } }, [SRGBColorSpace]: { primaries: e, whitePoint: i, transfer: SRGBTransfer, toXYZ: LINEAR_REC709_TO_XYZ, fromXYZ: XYZ_TO_LINEAR_REC709, luminanceCoefficients: r, outputColorSpaceConfig: { drawingBufferColorSpace: SRGBColorSpace } } }), t;
}
const ColorManagement = createColorManagement();
function SRGBToLinear(t) {
  return t < 0.04045 ? 0.0773993808 * t : Math.pow(0.9478672986 * t + 0.0521327014, 2.4);
}
function LinearToSRGB(t) {
  return t < 31308e-7 ? 12.92 * t : 1.055 * Math.pow(t, 0.41666) - 0.055;
}
class Vector4 {
  constructor(t = 0, e = 0, r = 0, i = 1) {
    Vector4.prototype.isVector4 = true, this.x = t, this.y = e, this.z = r, this.w = i;
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
  set(t, e, r, i) {
    return this.x = t, this.y = e, this.z = r, this.w = i, this;
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
    const e = this.x, r = this.y, i = this.z, s = this.w, n = t.elements;
    return this.x = n[0] * e + n[4] * r + n[8] * i + n[12] * s, this.y = n[1] * e + n[5] * r + n[9] * i + n[13] * s, this.z = n[2] * e + n[6] * r + n[10] * i + n[14] * s, this.w = n[3] * e + n[7] * r + n[11] * i + n[15] * s, this;
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
    let e, r, i, s;
    const n = 0.01, a = 0.1, o = t.elements, h = o[0], l = o[4], c = o[8], u = o[1], d = o[5], p = o[9], f = o[2], _ = o[6], y = o[10];
    if (Math.abs(l - u) < n && Math.abs(c - f) < n && Math.abs(p - _) < n) {
      if (Math.abs(l + u) < a && Math.abs(c + f) < a && Math.abs(p + _) < a && Math.abs(h + d + y - 3) < a)
        return this.set(1, 0, 0, 0), this;
      e = Math.PI;
      const t4 = (h + 1) / 2, o2 = (d + 1) / 2, m2 = (y + 1) / 2, g = (l + u) / 4, x = (c + f) / 4, M = (p + _) / 4;
      return t4 > o2 && t4 > m2 ? t4 < n ? (r = 0, i = 0.707106781, s = 0.707106781) : (r = Math.sqrt(t4), i = g / r, s = x / r) : o2 > m2 ? o2 < n ? (r = 0.707106781, i = 0, s = 0.707106781) : (i = Math.sqrt(o2), r = g / i, s = M / i) : m2 < n ? (r = 0.707106781, i = 0.707106781, s = 0) : (s = Math.sqrt(m2), r = x / s, i = M / s), this.set(r, i, s, e), this;
    }
    let m = Math.sqrt((_ - p) * (_ - p) + (c - f) * (c - f) + (u - l) * (u - l));
    return Math.abs(m) < 1e-3 && (m = 1), this.x = (_ - p) / m, this.y = (c - f) / m, this.z = (u - l) / m, this.w = Math.acos((h + d + y - 1) / 2), this;
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
    const r = this.length();
    return this.divideScalar(r || 1).multiplyScalar(clamp$1(r, t, e));
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
  lerpVectors(t, e, r) {
    return this.x = t.x + (e.x - t.x) * r, this.y = t.y + (e.y - t.y) * r, this.z = t.z + (e.z - t.z) * r, this.w = t.w + (e.w - t.w) * r, this;
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
    for (let e = 0, r = t.length; e < r; e += 3)
      this.expandByPoint(_vector$b.fromArray(t, e));
    return this;
  }
  setFromBufferAttribute(t) {
    this.makeEmpty();
    for (let e = 0, r = t.count; e < r; e++)
      this.expandByPoint(_vector$b.fromBufferAttribute(t, e));
    return this;
  }
  setFromPoints(t) {
    this.makeEmpty();
    for (let e = 0, r = t.length; e < r; e++)
      this.expandByPoint(t[e]);
    return this;
  }
  setFromCenterAndSize(t, e) {
    const r = _vector$b.copy(e).multiplyScalar(0.5);
    return this.min.copy(t).sub(r), this.max.copy(t).add(r), this;
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
    const r = t.geometry;
    if (void 0 !== r) {
      const i2 = r.getAttribute("position");
      if (true === e && void 0 !== i2 && true !== t.isInstancedMesh)
        for (let e2 = 0, r2 = i2.count; e2 < r2; e2++)
          true === t.isMesh ? t.getVertexPosition(e2, _vector$b) : _vector$b.fromBufferAttribute(i2, e2), _vector$b.applyMatrix4(t.matrixWorld), this.expandByPoint(_vector$b);
      else
        void 0 !== t.boundingBox ? (null === t.boundingBox && t.computeBoundingBox(), _box$4.copy(t.boundingBox)) : (null === r.boundingBox && r.computeBoundingBox(), _box$4.copy(r.boundingBox)), _box$4.applyMatrix4(t.matrixWorld), this.union(_box$4);
    }
    const i = t.children;
    for (let t4 = 0, r2 = i.length; t4 < r2; t4++)
      this.expandByObject(i[t4], e);
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
    let e, r;
    return t.normal.x > 0 ? (e = t.normal.x * this.min.x, r = t.normal.x * this.max.x) : (e = t.normal.x * this.max.x, r = t.normal.x * this.min.x), t.normal.y > 0 ? (e += t.normal.y * this.min.y, r += t.normal.y * this.max.y) : (e += t.normal.y * this.max.y, r += t.normal.y * this.min.y), t.normal.z > 0 ? (e += t.normal.z * this.min.z, r += t.normal.z * this.max.z) : (e += t.normal.z * this.max.z, r += t.normal.z * this.min.z), e <= -t.constant && r >= -t.constant;
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
function satForAxes(t, e, r, i, s) {
  for (let n = 0, a = t.length - 3; n <= a; n += 3) {
    _testAxis.fromArray(t, n);
    const a2 = s.x * Math.abs(_testAxis.x) + s.y * Math.abs(_testAxis.y) + s.z * Math.abs(_testAxis.z), o = e.dot(_testAxis), h = r.dot(_testAxis), l = i.dot(_testAxis);
    if (Math.max(-Math.max(o, h, l), Math.min(o, h, l)) > a2)
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
    const r = e.dot(this.direction);
    return r < 0 ? e.copy(this.origin) : e.copy(this.origin).addScaledVector(this.direction, r);
  }
  distanceToPoint(t) {
    return Math.sqrt(this.distanceSqToPoint(t));
  }
  distanceSqToPoint(t) {
    const e = _vector$a.subVectors(t, this.origin).dot(this.direction);
    return e < 0 ? this.origin.distanceToSquared(t) : (_vector$a.copy(this.origin).addScaledVector(this.direction, e), _vector$a.distanceToSquared(t));
  }
  distanceSqToSegment(t, e, r, i) {
    _segCenter.copy(t).add(e).multiplyScalar(0.5), _segDir.copy(e).sub(t).normalize(), _diff.copy(this.origin).sub(_segCenter);
    const s = 0.5 * t.distanceTo(e), n = -this.direction.dot(_segDir), a = _diff.dot(this.direction), o = -_diff.dot(_segDir), h = _diff.lengthSq(), l = Math.abs(1 - n * n);
    let c, u, d, p;
    if (l > 0)
      if (c = n * o - a, u = n * a - o, p = s * l, c >= 0)
        if (u >= -p)
          if (u <= p) {
            const t4 = 1 / l;
            c *= t4, u *= t4, d = c * (c + n * u + 2 * a) + u * (n * c + u + 2 * o) + h;
          } else
            u = s, c = Math.max(0, -(n * u + a)), d = -c * c + u * (u + 2 * o) + h;
        else
          u = -s, c = Math.max(0, -(n * u + a)), d = -c * c + u * (u + 2 * o) + h;
      else
        u <= -p ? (c = Math.max(0, -(-n * s + a)), u = c > 0 ? -s : Math.min(Math.max(-s, -o), s), d = -c * c + u * (u + 2 * o) + h) : u <= p ? (c = 0, u = Math.min(Math.max(-s, -o), s), d = u * (u + 2 * o) + h) : (c = Math.max(0, -(n * s + a)), u = c > 0 ? s : Math.min(Math.max(-s, -o), s), d = -c * c + u * (u + 2 * o) + h);
    else
      u = n > 0 ? -s : s, c = Math.max(0, -(n * u + a)), d = -c * c + u * (u + 2 * o) + h;
    return r && r.copy(this.origin).addScaledVector(this.direction, c), i && i.copy(_segCenter).addScaledVector(_segDir, u), d;
  }
  intersectSphere(t, e) {
    _vector$a.subVectors(t.center, this.origin);
    const r = _vector$a.dot(this.direction), i = _vector$a.dot(_vector$a) - r * r, s = t.radius * t.radius;
    if (i > s)
      return null;
    const n = Math.sqrt(s - i), a = r - n, o = r + n;
    return o < 0 ? null : a < 0 ? this.at(o, e) : this.at(a, e);
  }
  intersectsSphere(t) {
    return !(t.radius < 0) && this.distanceSqToPoint(t.center) <= t.radius * t.radius;
  }
  distanceToPlane(t) {
    const e = t.normal.dot(this.direction);
    if (0 === e)
      return 0 === t.distanceToPoint(this.origin) ? 0 : null;
    const r = -(this.origin.dot(t.normal) + t.constant) / e;
    return r >= 0 ? r : null;
  }
  intersectPlane(t, e) {
    const r = this.distanceToPlane(t);
    return null === r ? null : this.at(r, e);
  }
  intersectsPlane(t) {
    const e = t.distanceToPoint(this.origin);
    if (0 === e)
      return true;
    return t.normal.dot(this.direction) * e < 0;
  }
  intersectBox(t, e) {
    let r, i, s, n, a, o;
    const h = 1 / this.direction.x, l = 1 / this.direction.y, c = 1 / this.direction.z, u = this.origin;
    return h >= 0 ? (r = (t.min.x - u.x) * h, i = (t.max.x - u.x) * h) : (r = (t.max.x - u.x) * h, i = (t.min.x - u.x) * h), l >= 0 ? (s = (t.min.y - u.y) * l, n = (t.max.y - u.y) * l) : (s = (t.max.y - u.y) * l, n = (t.min.y - u.y) * l), r > n || s > i ? null : ((s > r || isNaN(r)) && (r = s), (n < i || isNaN(i)) && (i = n), c >= 0 ? (a = (t.min.z - u.z) * c, o = (t.max.z - u.z) * c) : (a = (t.max.z - u.z) * c, o = (t.min.z - u.z) * c), r > o || a > i ? null : ((a > r || r != r) && (r = a), (o < i || i != i) && (i = o), i < 0 ? null : this.at(r >= 0 ? r : i, e)));
  }
  intersectsBox(t) {
    return null !== this.intersectBox(t, _vector$a);
  }
  intersectTriangle(t, e, r, i, s) {
    _edge1.subVectors(e, t), _edge2.subVectors(r, t), _normal$1.crossVectors(_edge1, _edge2);
    let n, a = this.direction.dot(_normal$1);
    if (a > 0) {
      if (i)
        return null;
      n = 1;
    } else {
      if (!(a < 0))
        return null;
      n = -1, a = -a;
    }
    _diff.subVectors(this.origin, t);
    const o = n * this.direction.dot(_edge2.crossVectors(_diff, _edge2));
    if (o < 0)
      return null;
    const h = n * this.direction.dot(_edge1.cross(_diff));
    if (h < 0)
      return null;
    if (o + h > a)
      return null;
    const l = -n * _diff.dot(_normal$1);
    return l < 0 ? null : this.at(l / a, s);
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
  constructor(t, e, r, i, s, n, a, o, h, l, c, u, d, p, f, _) {
    Matrix4.prototype.isMatrix4 = true, this.elements = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1], void 0 !== t && this.set(t, e, r, i, s, n, a, o, h, l, c, u, d, p, f, _);
  }
  set(t, e, r, i, s, n, a, o, h, l, c, u, d, p, f, _) {
    const y = this.elements;
    return y[0] = t, y[4] = e, y[8] = r, y[12] = i, y[1] = s, y[5] = n, y[9] = a, y[13] = o, y[2] = h, y[6] = l, y[10] = c, y[14] = u, y[3] = d, y[7] = p, y[11] = f, y[15] = _, this;
  }
  identity() {
    return this.set(1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1), this;
  }
  clone() {
    return new Matrix4().fromArray(this.elements);
  }
  copy(t) {
    const e = this.elements, r = t.elements;
    return e[0] = r[0], e[1] = r[1], e[2] = r[2], e[3] = r[3], e[4] = r[4], e[5] = r[5], e[6] = r[6], e[7] = r[7], e[8] = r[8], e[9] = r[9], e[10] = r[10], e[11] = r[11], e[12] = r[12], e[13] = r[13], e[14] = r[14], e[15] = r[15], this;
  }
  copyPosition(t) {
    const e = this.elements, r = t.elements;
    return e[12] = r[12], e[13] = r[13], e[14] = r[14], this;
  }
  setFromMatrix3(t) {
    const e = t.elements;
    return this.set(e[0], e[3], e[6], 0, e[1], e[4], e[7], 0, e[2], e[5], e[8], 0, 0, 0, 0, 1), this;
  }
  extractBasis(t, e, r) {
    return t.setFromMatrixColumn(this, 0), e.setFromMatrixColumn(this, 1), r.setFromMatrixColumn(this, 2), this;
  }
  makeBasis(t, e, r) {
    return this.set(t.x, e.x, r.x, 0, t.y, e.y, r.y, 0, t.z, e.z, r.z, 0, 0, 0, 0, 1), this;
  }
  extractRotation(t) {
    const e = this.elements, r = t.elements, i = 1 / _v1$5.setFromMatrixColumn(t, 0).length(), s = 1 / _v1$5.setFromMatrixColumn(t, 1).length(), n = 1 / _v1$5.setFromMatrixColumn(t, 2).length();
    return e[0] = r[0] * i, e[1] = r[1] * i, e[2] = r[2] * i, e[3] = 0, e[4] = r[4] * s, e[5] = r[5] * s, e[6] = r[6] * s, e[7] = 0, e[8] = r[8] * n, e[9] = r[9] * n, e[10] = r[10] * n, e[11] = 0, e[12] = 0, e[13] = 0, e[14] = 0, e[15] = 1, this;
  }
  makeRotationFromEuler(t) {
    const e = this.elements, r = t.x, i = t.y, s = t.z, n = Math.cos(r), a = Math.sin(r), o = Math.cos(i), h = Math.sin(i), l = Math.cos(s), c = Math.sin(s);
    if ("XYZ" === t.order) {
      const t4 = n * l, r2 = n * c, i2 = a * l, s2 = a * c;
      e[0] = o * l, e[4] = -o * c, e[8] = h, e[1] = r2 + i2 * h, e[5] = t4 - s2 * h, e[9] = -a * o, e[2] = s2 - t4 * h, e[6] = i2 + r2 * h, e[10] = n * o;
    } else if ("YXZ" === t.order) {
      const t4 = o * l, r2 = o * c, i2 = h * l, s2 = h * c;
      e[0] = t4 + s2 * a, e[4] = i2 * a - r2, e[8] = n * h, e[1] = n * c, e[5] = n * l, e[9] = -a, e[2] = r2 * a - i2, e[6] = s2 + t4 * a, e[10] = n * o;
    } else if ("ZXY" === t.order) {
      const t4 = o * l, r2 = o * c, i2 = h * l, s2 = h * c;
      e[0] = t4 - s2 * a, e[4] = -n * c, e[8] = i2 + r2 * a, e[1] = r2 + i2 * a, e[5] = n * l, e[9] = s2 - t4 * a, e[2] = -n * h, e[6] = a, e[10] = n * o;
    } else if ("ZYX" === t.order) {
      const t4 = n * l, r2 = n * c, i2 = a * l, s2 = a * c;
      e[0] = o * l, e[4] = i2 * h - r2, e[8] = t4 * h + s2, e[1] = o * c, e[5] = s2 * h + t4, e[9] = r2 * h - i2, e[2] = -h, e[6] = a * o, e[10] = n * o;
    } else if ("YZX" === t.order) {
      const t4 = n * o, r2 = n * h, i2 = a * o, s2 = a * h;
      e[0] = o * l, e[4] = s2 - t4 * c, e[8] = i2 * c + r2, e[1] = c, e[5] = n * l, e[9] = -a * l, e[2] = -h * l, e[6] = r2 * c + i2, e[10] = t4 - s2 * c;
    } else if ("XZY" === t.order) {
      const t4 = n * o, r2 = n * h, i2 = a * o, s2 = a * h;
      e[0] = o * l, e[4] = -c, e[8] = h * l, e[1] = t4 * c + s2, e[5] = n * l, e[9] = r2 * c - i2, e[2] = i2 * c - r2, e[6] = a * l, e[10] = s2 * c + t4;
    }
    return e[3] = 0, e[7] = 0, e[11] = 0, e[12] = 0, e[13] = 0, e[14] = 0, e[15] = 1, this;
  }
  makeRotationFromQuaternion(t) {
    return this.compose(_zero, t, _one);
  }
  lookAt(t, e, r) {
    const i = this.elements;
    return _z.subVectors(t, e), 0 === _z.lengthSq() && (_z.z = 1), _z.normalize(), _x.crossVectors(r, _z), 0 === _x.lengthSq() && (1 === Math.abs(r.z) ? _z.x += 1e-4 : _z.z += 1e-4, _z.normalize(), _x.crossVectors(r, _z)), _x.normalize(), _y.crossVectors(_z, _x), i[0] = _x.x, i[4] = _y.x, i[8] = _z.x, i[1] = _x.y, i[5] = _y.y, i[9] = _z.y, i[2] = _x.z, i[6] = _y.z, i[10] = _z.z, this;
  }
  multiply(t) {
    return this.multiplyMatrices(this, t);
  }
  premultiply(t) {
    return this.multiplyMatrices(t, this);
  }
  multiplyMatrices(t, e) {
    const r = t.elements, i = e.elements, s = this.elements, n = r[0], a = r[4], o = r[8], h = r[12], l = r[1], c = r[5], u = r[9], d = r[13], p = r[2], f = r[6], _ = r[10], y = r[14], m = r[3], g = r[7], x = r[11], M = r[15], w = i[0], b = i[4], v = i[8], S = i[12], C = i[1], E = i[5], P = i[9], T = i[13], I = i[2], z = i[6], A2 = i[10], O = i[14], N = i[3], R = i[7], $ = i[11], V = i[15];
    return s[0] = n * w + a * C + o * I + h * N, s[4] = n * b + a * E + o * z + h * R, s[8] = n * v + a * P + o * A2 + h * $, s[12] = n * S + a * T + o * O + h * V, s[1] = l * w + c * C + u * I + d * N, s[5] = l * b + c * E + u * z + d * R, s[9] = l * v + c * P + u * A2 + d * $, s[13] = l * S + c * T + u * O + d * V, s[2] = p * w + f * C + _ * I + y * N, s[6] = p * b + f * E + _ * z + y * R, s[10] = p * v + f * P + _ * A2 + y * $, s[14] = p * S + f * T + _ * O + y * V, s[3] = m * w + g * C + x * I + M * N, s[7] = m * b + g * E + x * z + M * R, s[11] = m * v + g * P + x * A2 + M * $, s[15] = m * S + g * T + x * O + M * V, this;
  }
  multiplyScalar(t) {
    const e = this.elements;
    return e[0] *= t, e[4] *= t, e[8] *= t, e[12] *= t, e[1] *= t, e[5] *= t, e[9] *= t, e[13] *= t, e[2] *= t, e[6] *= t, e[10] *= t, e[14] *= t, e[3] *= t, e[7] *= t, e[11] *= t, e[15] *= t, this;
  }
  determinant() {
    const t = this.elements, e = t[0], r = t[4], i = t[8], s = t[12], n = t[1], a = t[5], o = t[9], h = t[13], l = t[2], c = t[6], u = t[10], d = t[14];
    return t[3] * (+s * o * c - i * h * c - s * a * u + r * h * u + i * a * d - r * o * d) + t[7] * (+e * o * d - e * h * u + s * n * u - i * n * d + i * h * l - s * o * l) + t[11] * (+e * h * c - e * a * d - s * n * c + r * n * d + s * a * l - r * h * l) + t[15] * (-i * a * l - e * o * c + e * a * u + i * n * c - r * n * u + r * o * l);
  }
  transpose() {
    const t = this.elements;
    let e;
    return e = t[1], t[1] = t[4], t[4] = e, e = t[2], t[2] = t[8], t[8] = e, e = t[6], t[6] = t[9], t[9] = e, e = t[3], t[3] = t[12], t[12] = e, e = t[7], t[7] = t[13], t[13] = e, e = t[11], t[11] = t[14], t[14] = e, this;
  }
  setPosition(t, e, r) {
    const i = this.elements;
    return t.isVector3 ? (i[12] = t.x, i[13] = t.y, i[14] = t.z) : (i[12] = t, i[13] = e, i[14] = r), this;
  }
  invert() {
    const t = this.elements, e = t[0], r = t[1], i = t[2], s = t[3], n = t[4], a = t[5], o = t[6], h = t[7], l = t[8], c = t[9], u = t[10], d = t[11], p = t[12], f = t[13], _ = t[14], y = t[15], m = c * _ * h - f * u * h + f * o * d - a * _ * d - c * o * y + a * u * y, g = p * u * h - l * _ * h - p * o * d + n * _ * d + l * o * y - n * u * y, x = l * f * h - p * c * h + p * a * d - n * f * d - l * a * y + n * c * y, M = p * c * o - l * f * o - p * a * u + n * f * u + l * a * _ - n * c * _, w = e * m + r * g + i * x + s * M;
    if (0 === w)
      return this.set(0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0);
    const b = 1 / w;
    return t[0] = m * b, t[1] = (f * u * s - c * _ * s - f * i * d + r * _ * d + c * i * y - r * u * y) * b, t[2] = (a * _ * s - f * o * s + f * i * h - r * _ * h - a * i * y + r * o * y) * b, t[3] = (c * o * s - a * u * s - c * i * h + r * u * h + a * i * d - r * o * d) * b, t[4] = g * b, t[5] = (l * _ * s - p * u * s + p * i * d - e * _ * d - l * i * y + e * u * y) * b, t[6] = (p * o * s - n * _ * s - p * i * h + e * _ * h + n * i * y - e * o * y) * b, t[7] = (n * u * s - l * o * s + l * i * h - e * u * h - n * i * d + e * o * d) * b, t[8] = x * b, t[9] = (p * c * s - l * f * s - p * r * d + e * f * d + l * r * y - e * c * y) * b, t[10] = (n * f * s - p * a * s + p * r * h - e * f * h - n * r * y + e * a * y) * b, t[11] = (l * a * s - n * c * s - l * r * h + e * c * h + n * r * d - e * a * d) * b, t[12] = M * b, t[13] = (l * f * i - p * c * i + p * r * u - e * f * u - l * r * _ + e * c * _) * b, t[14] = (p * a * i - n * f * i - p * r * o + e * f * o + n * r * _ - e * a * _) * b, t[15] = (n * c * i - l * a * i + l * r * o - e * c * o - n * r * u + e * a * u) * b, this;
  }
  scale(t) {
    const e = this.elements, r = t.x, i = t.y, s = t.z;
    return e[0] *= r, e[4] *= i, e[8] *= s, e[1] *= r, e[5] *= i, e[9] *= s, e[2] *= r, e[6] *= i, e[10] *= s, e[3] *= r, e[7] *= i, e[11] *= s, this;
  }
  getMaxScaleOnAxis() {
    const t = this.elements, e = t[0] * t[0] + t[1] * t[1] + t[2] * t[2], r = t[4] * t[4] + t[5] * t[5] + t[6] * t[6], i = t[8] * t[8] + t[9] * t[9] + t[10] * t[10];
    return Math.sqrt(Math.max(e, r, i));
  }
  makeTranslation(t, e, r) {
    return t.isVector3 ? this.set(1, 0, 0, t.x, 0, 1, 0, t.y, 0, 0, 1, t.z, 0, 0, 0, 1) : this.set(1, 0, 0, t, 0, 1, 0, e, 0, 0, 1, r, 0, 0, 0, 1), this;
  }
  makeRotationX(t) {
    const e = Math.cos(t), r = Math.sin(t);
    return this.set(1, 0, 0, 0, 0, e, -r, 0, 0, r, e, 0, 0, 0, 0, 1), this;
  }
  makeRotationY(t) {
    const e = Math.cos(t), r = Math.sin(t);
    return this.set(e, 0, r, 0, 0, 1, 0, 0, -r, 0, e, 0, 0, 0, 0, 1), this;
  }
  makeRotationZ(t) {
    const e = Math.cos(t), r = Math.sin(t);
    return this.set(e, -r, 0, 0, r, e, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1), this;
  }
  makeRotationAxis(t, e) {
    const r = Math.cos(e), i = Math.sin(e), s = 1 - r, n = t.x, a = t.y, o = t.z, h = s * n, l = s * a;
    return this.set(h * n + r, h * a - i * o, h * o + i * a, 0, h * a + i * o, l * a + r, l * o - i * n, 0, h * o - i * a, l * o + i * n, s * o * o + r, 0, 0, 0, 0, 1), this;
  }
  makeScale(t, e, r) {
    return this.set(t, 0, 0, 0, 0, e, 0, 0, 0, 0, r, 0, 0, 0, 0, 1), this;
  }
  makeShear(t, e, r, i, s, n) {
    return this.set(1, r, s, 0, t, 1, n, 0, e, i, 1, 0, 0, 0, 0, 1), this;
  }
  compose(t, e, r) {
    const i = this.elements, s = e._x, n = e._y, a = e._z, o = e._w, h = s + s, l = n + n, c = a + a, u = s * h, d = s * l, p = s * c, f = n * l, _ = n * c, y = a * c, m = o * h, g = o * l, x = o * c, M = r.x, w = r.y, b = r.z;
    return i[0] = (1 - (f + y)) * M, i[1] = (d + x) * M, i[2] = (p - g) * M, i[3] = 0, i[4] = (d - x) * w, i[5] = (1 - (u + y)) * w, i[6] = (_ + m) * w, i[7] = 0, i[8] = (p + g) * b, i[9] = (_ - m) * b, i[10] = (1 - (u + f)) * b, i[11] = 0, i[12] = t.x, i[13] = t.y, i[14] = t.z, i[15] = 1, this;
  }
  decompose(t, e, r) {
    const i = this.elements;
    let s = _v1$5.set(i[0], i[1], i[2]).length();
    const n = _v1$5.set(i[4], i[5], i[6]).length(), a = _v1$5.set(i[8], i[9], i[10]).length();
    this.determinant() < 0 && (s = -s), t.x = i[12], t.y = i[13], t.z = i[14], _m1$2.copy(this);
    const o = 1 / s, h = 1 / n, l = 1 / a;
    return _m1$2.elements[0] *= o, _m1$2.elements[1] *= o, _m1$2.elements[2] *= o, _m1$2.elements[4] *= h, _m1$2.elements[5] *= h, _m1$2.elements[6] *= h, _m1$2.elements[8] *= l, _m1$2.elements[9] *= l, _m1$2.elements[10] *= l, e.setFromRotationMatrix(_m1$2), r.x = s, r.y = n, r.z = a, this;
  }
  makePerspective(t, e, r, i, s, n, a = WebGLCoordinateSystem, o = false) {
    const h = this.elements, l = 2 * s / (e - t), c = 2 * s / (r - i), u = (e + t) / (e - t), d = (r + i) / (r - i);
    let p, f;
    if (o)
      p = s / (n - s), f = n * s / (n - s);
    else if (a === WebGLCoordinateSystem)
      p = -(n + s) / (n - s), f = -2 * n * s / (n - s);
    else {
      if (a !== WebGPUCoordinateSystem)
        throw new Error("THREE.Matrix4.makePerspective(): Invalid coordinate system: " + a);
      p = -n / (n - s), f = -n * s / (n - s);
    }
    return h[0] = l, h[4] = 0, h[8] = u, h[12] = 0, h[1] = 0, h[5] = c, h[9] = d, h[13] = 0, h[2] = 0, h[6] = 0, h[10] = p, h[14] = f, h[3] = 0, h[7] = 0, h[11] = -1, h[15] = 0, this;
  }
  makeOrthographic(t, e, r, i, s, n, a = WebGLCoordinateSystem, o = false) {
    const h = this.elements, l = 2 / (e - t), c = 2 / (r - i), u = -(e + t) / (e - t), d = -(r + i) / (r - i);
    let p, f;
    if (o)
      p = 1 / (n - s), f = n / (n - s);
    else if (a === WebGLCoordinateSystem)
      p = -2 / (n - s), f = -(n + s) / (n - s);
    else {
      if (a !== WebGPUCoordinateSystem)
        throw new Error("THREE.Matrix4.makeOrthographic(): Invalid coordinate system: " + a);
      p = -1 / (n - s), f = -s / (n - s);
    }
    return h[0] = l, h[4] = 0, h[8] = 0, h[12] = u, h[1] = 0, h[5] = c, h[9] = 0, h[13] = d, h[2] = 0, h[6] = 0, h[10] = p, h[14] = f, h[3] = 0, h[7] = 0, h[11] = 0, h[15] = 1, this;
  }
  equals(t) {
    const e = this.elements, r = t.elements;
    for (let t4 = 0; t4 < 16; t4++)
      if (e[t4] !== r[t4])
        return false;
    return true;
  }
  fromArray(t, e = 0) {
    for (let r = 0; r < 16; r++)
      this.elements[r] = t[r + e];
    return this;
  }
  toArray(t = [], e = 0) {
    const r = this.elements;
    return t[e] = r[0], t[e + 1] = r[1], t[e + 2] = r[2], t[e + 3] = r[3], t[e + 4] = r[4], t[e + 5] = r[5], t[e + 6] = r[6], t[e + 7] = r[7], t[e + 8] = r[8], t[e + 9] = r[9], t[e + 10] = r[10], t[e + 11] = r[11], t[e + 12] = r[12], t[e + 13] = r[13], t[e + 14] = r[14], t[e + 15] = r[15], t;
  }
}
const _v1$5 = new Vector3$1(), _m1$2 = new Matrix4(), _zero = new Vector3$1(0, 0, 0), _one = new Vector3$1(1, 1, 1), _x = new Vector3$1(), _y = new Vector3$1(), _z = new Vector3$1(), _colorKeywords = { aliceblue: 15792383, antiquewhite: 16444375, aqua: 65535, aquamarine: 8388564, azure: 15794175, beige: 16119260, bisque: 16770244, black: 0, blanchedalmond: 16772045, blue: 255, blueviolet: 9055202, brown: 10824234, burlywood: 14596231, cadetblue: 6266528, chartreuse: 8388352, chocolate: 13789470, coral: 16744272, cornflowerblue: 6591981, cornsilk: 16775388, crimson: 14423100, cyan: 65535, darkblue: 139, darkcyan: 35723, darkgoldenrod: 12092939, darkgray: 11119017, darkgreen: 25600, darkgrey: 11119017, darkkhaki: 12433259, darkmagenta: 9109643, darkolivegreen: 5597999, darkorange: 16747520, darkorchid: 10040012, darkred: 9109504, darksalmon: 15308410, darkseagreen: 9419919, darkslateblue: 4734347, darkslategray: 3100495, darkslategrey: 3100495, darkturquoise: 52945, darkviolet: 9699539, deeppink: 16716947, deepskyblue: 49151, dimgray: 6908265, dimgrey: 6908265, dodgerblue: 2003199, firebrick: 11674146, floralwhite: 16775920, forestgreen: 2263842, fuchsia: 16711935, gainsboro: 14474460, ghostwhite: 16316671, gold: 16766720, goldenrod: 14329120, gray: 8421504, green: 32768, greenyellow: 11403055, grey: 8421504, honeydew: 15794160, hotpink: 16738740, indianred: 13458524, indigo: 4915330, ivory: 16777200, khaki: 15787660, lavender: 15132410, lavenderblush: 16773365, lawngreen: 8190976, lemonchiffon: 16775885, lightblue: 11393254, lightcoral: 15761536, lightcyan: 14745599, lightgoldenrodyellow: 16448210, lightgray: 13882323, lightgreen: 9498256, lightgrey: 13882323, lightpink: 16758465, lightsalmon: 16752762, lightseagreen: 2142890, lightskyblue: 8900346, lightslategray: 7833753, lightslategrey: 7833753, lightsteelblue: 11584734, lightyellow: 16777184, lime: 65280, limegreen: 3329330, linen: 16445670, magenta: 16711935, maroon: 8388608, mediumaquamarine: 6737322, mediumblue: 205, mediumorchid: 12211667, mediumpurple: 9662683, mediumseagreen: 3978097, mediumslateblue: 8087790, mediumspringgreen: 64154, mediumturquoise: 4772300, mediumvioletred: 13047173, midnightblue: 1644912, mintcream: 16121850, mistyrose: 16770273, moccasin: 16770229, navajowhite: 16768685, navy: 128, oldlace: 16643558, olive: 8421376, olivedrab: 7048739, orange: 16753920, orangered: 16729344, orchid: 14315734, palegoldenrod: 15657130, palegreen: 10025880, paleturquoise: 11529966, palevioletred: 14381203, papayawhip: 16773077, peachpuff: 16767673, peru: 13468991, pink: 16761035, plum: 14524637, powderblue: 11591910, purple: 8388736, rebeccapurple: 6697881, red: 16711680, rosybrown: 12357519, royalblue: 4286945, saddlebrown: 9127187, salmon: 16416882, sandybrown: 16032864, seagreen: 3050327, seashell: 16774638, sienna: 10506797, silver: 12632256, skyblue: 8900331, slateblue: 6970061, slategray: 7372944, slategrey: 7372944, snow: 16775930, springgreen: 65407, steelblue: 4620980, tan: 13808780, teal: 32896, thistle: 14204888, tomato: 16737095, turquoise: 4251856, violet: 15631086, wheat: 16113331, white: 16777215, whitesmoke: 16119285, yellow: 16776960, yellowgreen: 10145074 }, _hslA = { h: 0, s: 0, l: 0 }, _hslB = { h: 0, s: 0, l: 0 };
function hue2rgb(t, e, r) {
  return r < 0 && (r += 1), r > 1 && (r -= 1), r < 1 / 6 ? t + 6 * (e - t) * r : r < 0.5 ? e : r < 2 / 3 ? t + 6 * (e - t) * (2 / 3 - r) : t;
}
class Color$1 {
  constructor(t, e, r) {
    return this.isColor = true, this.r = 1, this.g = 1, this.b = 1, this.set(t, e, r);
  }
  set(t, e, r) {
    if (void 0 === e && void 0 === r) {
      const e2 = t;
      e2 && e2.isColor ? this.copy(e2) : "number" == typeof e2 ? this.setHex(e2) : "string" == typeof e2 && this.setStyle(e2);
    } else
      this.setRGB(t, e, r);
    return this;
  }
  setScalar(t) {
    return this.r = t, this.g = t, this.b = t, this;
  }
  setHex(t, e = SRGBColorSpace) {
    return t = Math.floor(t), this.r = (t >> 16 & 255) / 255, this.g = (t >> 8 & 255) / 255, this.b = (255 & t) / 255, ColorManagement.colorSpaceToWorking(this, e), this;
  }
  setRGB(t, e, r, i = ColorManagement.workingColorSpace) {
    return this.r = t, this.g = e, this.b = r, ColorManagement.colorSpaceToWorking(this, i), this;
  }
  setHSL(t, e, r, i = ColorManagement.workingColorSpace) {
    if (t = euclideanModulo(t, 1), e = clamp$1(e, 0, 1), r = clamp$1(r, 0, 1), 0 === e)
      this.r = this.g = this.b = r;
    else {
      const i2 = r <= 0.5 ? r * (1 + e) : r + e - r * e, s = 2 * r - i2;
      this.r = hue2rgb(s, i2, t + 1 / 3), this.g = hue2rgb(s, i2, t), this.b = hue2rgb(s, i2, t - 1 / 3);
    }
    return ColorManagement.colorSpaceToWorking(this, i), this;
  }
  setStyle(t, e = SRGBColorSpace) {
    function r(e2) {
      void 0 !== e2 && parseFloat(e2) < 1 && console.warn("THREE.Color: Alpha component of " + t + " will be ignored.");
    }
    let i;
    if (i = /^(\w+)\(([^\)]*)\)/.exec(t)) {
      let s;
      const n = i[1], a = i[2];
      switch (n) {
        case "rgb":
        case "rgba":
          if (s = /^\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*(?:,\s*(\d*\.?\d+)\s*)?$/.exec(a))
            return r(s[4]), this.setRGB(Math.min(255, parseInt(s[1], 10)) / 255, Math.min(255, parseInt(s[2], 10)) / 255, Math.min(255, parseInt(s[3], 10)) / 255, e);
          if (s = /^\s*(\d+)\%\s*,\s*(\d+)\%\s*,\s*(\d+)\%\s*(?:,\s*(\d*\.?\d+)\s*)?$/.exec(a))
            return r(s[4]), this.setRGB(Math.min(100, parseInt(s[1], 10)) / 100, Math.min(100, parseInt(s[2], 10)) / 100, Math.min(100, parseInt(s[3], 10)) / 100, e);
          break;
        case "hsl":
        case "hsla":
          if (s = /^\s*(\d*\.?\d+)\s*,\s*(\d*\.?\d+)\%\s*,\s*(\d*\.?\d+)\%\s*(?:,\s*(\d*\.?\d+)\s*)?$/.exec(a))
            return r(s[4]), this.setHSL(parseFloat(s[1]) / 360, parseFloat(s[2]) / 100, parseFloat(s[3]) / 100, e);
          break;
        default:
          console.warn("THREE.Color: Unknown color model " + t);
      }
    } else if (i = /^\#([A-Fa-f\d]+)$/.exec(t)) {
      const r2 = i[1], s = r2.length;
      if (3 === s)
        return this.setRGB(parseInt(r2.charAt(0), 16) / 15, parseInt(r2.charAt(1), 16) / 15, parseInt(r2.charAt(2), 16) / 15, e);
      if (6 === s)
        return this.setHex(parseInt(r2, 16), e);
      console.warn("THREE.Color: Invalid hex color " + t);
    } else if (t && t.length > 0)
      return this.setColorName(t, e);
    return this;
  }
  setColorName(t, e = SRGBColorSpace) {
    const r = _colorKeywords[t.toLowerCase()];
    return void 0 !== r ? this.setHex(r, e) : console.warn("THREE.Color: Unknown color " + t), this;
  }
  clone() {
    return new this.constructor(this.r, this.g, this.b);
  }
  copy(t) {
    return this.r = t.r, this.g = t.g, this.b = t.b, this;
  }
  copySRGBToLinear(t) {
    return this.r = SRGBToLinear(t.r), this.g = SRGBToLinear(t.g), this.b = SRGBToLinear(t.b), this;
  }
  copyLinearToSRGB(t) {
    return this.r = LinearToSRGB(t.r), this.g = LinearToSRGB(t.g), this.b = LinearToSRGB(t.b), this;
  }
  convertSRGBToLinear() {
    return this.copySRGBToLinear(this), this;
  }
  convertLinearToSRGB() {
    return this.copyLinearToSRGB(this), this;
  }
  getHex(t = SRGBColorSpace) {
    return ColorManagement.workingToColorSpace(_color.copy(this), t), 65536 * Math.round(clamp$1(255 * _color.r, 0, 255)) + 256 * Math.round(clamp$1(255 * _color.g, 0, 255)) + Math.round(clamp$1(255 * _color.b, 0, 255));
  }
  getHexString(t = SRGBColorSpace) {
    return ("000000" + this.getHex(t).toString(16)).slice(-6);
  }
  getHSL(t, e = ColorManagement.workingColorSpace) {
    ColorManagement.workingToColorSpace(_color.copy(this), e);
    const r = _color.r, i = _color.g, s = _color.b, n = Math.max(r, i, s), a = Math.min(r, i, s);
    let o, h;
    const l = (a + n) / 2;
    if (a === n)
      o = 0, h = 0;
    else {
      const t4 = n - a;
      switch (h = l <= 0.5 ? t4 / (n + a) : t4 / (2 - n - a), n) {
        case r:
          o = (i - s) / t4 + (i < s ? 6 : 0);
          break;
        case i:
          o = (s - r) / t4 + 2;
          break;
        case s:
          o = (r - i) / t4 + 4;
      }
      o /= 6;
    }
    return t.h = o, t.s = h, t.l = l, t;
  }
  getRGB(t, e = ColorManagement.workingColorSpace) {
    return ColorManagement.workingToColorSpace(_color.copy(this), e), t.r = _color.r, t.g = _color.g, t.b = _color.b, t;
  }
  getStyle(t = SRGBColorSpace) {
    ColorManagement.workingToColorSpace(_color.copy(this), t);
    const e = _color.r, r = _color.g, i = _color.b;
    return t !== SRGBColorSpace ? `color(${t} ${e.toFixed(3)} ${r.toFixed(3)} ${i.toFixed(3)})` : `rgb(${Math.round(255 * e)},${Math.round(255 * r)},${Math.round(255 * i)})`;
  }
  offsetHSL(t, e, r) {
    return this.getHSL(_hslA), this.setHSL(_hslA.h + t, _hslA.s + e, _hslA.l + r);
  }
  add(t) {
    return this.r += t.r, this.g += t.g, this.b += t.b, this;
  }
  addColors(t, e) {
    return this.r = t.r + e.r, this.g = t.g + e.g, this.b = t.b + e.b, this;
  }
  addScalar(t) {
    return this.r += t, this.g += t, this.b += t, this;
  }
  sub(t) {
    return this.r = Math.max(0, this.r - t.r), this.g = Math.max(0, this.g - t.g), this.b = Math.max(0, this.b - t.b), this;
  }
  multiply(t) {
    return this.r *= t.r, this.g *= t.g, this.b *= t.b, this;
  }
  multiplyScalar(t) {
    return this.r *= t, this.g *= t, this.b *= t, this;
  }
  lerp(t, e) {
    return this.r += (t.r - this.r) * e, this.g += (t.g - this.g) * e, this.b += (t.b - this.b) * e, this;
  }
  lerpColors(t, e, r) {
    return this.r = t.r + (e.r - t.r) * r, this.g = t.g + (e.g - t.g) * r, this.b = t.b + (e.b - t.b) * r, this;
  }
  lerpHSL(t, e) {
    this.getHSL(_hslA), t.getHSL(_hslB);
    const r = lerp(_hslA.h, _hslB.h, e), i = lerp(_hslA.s, _hslB.s, e), s = lerp(_hslA.l, _hslB.l, e);
    return this.setHSL(r, i, s), this;
  }
  setFromVector3(t) {
    return this.r = t.x, this.g = t.y, this.b = t.z, this;
  }
  applyMatrix3(t) {
    const e = this.r, r = this.g, i = this.b, s = t.elements;
    return this.r = s[0] * e + s[3] * r + s[6] * i, this.g = s[1] * e + s[4] * r + s[7] * i, this.b = s[2] * e + s[5] * r + s[8] * i, this;
  }
  equals(t) {
    return t.r === this.r && t.g === this.g && t.b === this.b;
  }
  fromArray(t, e = 0) {
    return this.r = t[e], this.g = t[e + 1], this.b = t[e + 2], this;
  }
  toArray(t = [], e = 0) {
    return t[e] = this.r, t[e + 1] = this.g, t[e + 2] = this.b, t;
  }
  fromBufferAttribute(t, e) {
    return this.r = t.getX(e), this.g = t.getY(e), this.b = t.getZ(e), this;
  }
  toJSON() {
    return this.getHex();
  }
  *[Symbol.iterator]() {
    yield this.r, yield this.g, yield this.b;
  }
}
const _color = new Color$1();
Color$1.NAMES = _colorKeywords;
const _vector1 = new Vector3$1(), _vector2 = new Vector3$1(), _normalMatrix = new Matrix3();
class Plane {
  constructor(t = new Vector3$1(1, 0, 0), e = 0) {
    this.isPlane = true, this.normal = t, this.constant = e;
  }
  set(t, e) {
    return this.normal.copy(t), this.constant = e, this;
  }
  setComponents(t, e, r, i) {
    return this.normal.set(t, e, r), this.constant = i, this;
  }
  setFromNormalAndCoplanarPoint(t, e) {
    return this.normal.copy(t), this.constant = -e.dot(this.normal), this;
  }
  setFromCoplanarPoints(t, e, r) {
    const i = _vector1.subVectors(r, e).cross(_vector2.subVectors(t, e)).normalize();
    return this.setFromNormalAndCoplanarPoint(i, t), this;
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
    const r = t.delta(_vector1), i = this.normal.dot(r);
    if (0 === i)
      return 0 === this.distanceToPoint(t.start) ? e.copy(t.start) : null;
    const s = -(t.start.dot(this.normal) + this.constant) / i;
    return s < 0 || s > 1 ? null : e.copy(t.start).addScaledVector(r, s);
  }
  intersectsLine(t) {
    const e = this.distanceToPoint(t.start), r = this.distanceToPoint(t.end);
    return e < 0 && r > 0 || r < 0 && e > 0;
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
    const r = e || _normalMatrix.getNormalMatrix(t), i = this.coplanarPoint(_vector1).applyMatrix4(t), s = this.normal.applyMatrix3(r).normalize();
    return this.constant = -i.dot(s), this;
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
function defined$2(t) {
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
  } catch (t4) {
    e = t4.stack;
  }
  this.stack = e;
}
"undefined" != typeof __THREE_DEVTOOLS__ && __THREE_DEVTOOLS__.dispatchEvent(new CustomEvent("register", { detail: { revision: REVISION } })), "undefined" != typeof window && (window.__THREE__ ? console.warn("WARNING: Multiple instances of Three.js being imported.") : window.__THREE__ = REVISION), defaultValue.EMPTY_OBJECT = Object.freeze({}), defined$2(Object.create) && (DeveloperError.prototype = Object.create(Error.prototype), DeveloperError.prototype.constructor = DeveloperError), DeveloperError.prototype.toString = function() {
  let t = this.name + ": " + this.message;
  return defined$2(this.stack) && (t += "\n" + this.stack.toString()), t;
}, DeveloperError.throwInstantiationError = function() {
  throw new DeveloperError("This function defines an interface and should not be called directly.");
};
const _CesiumMath = class {
  static equalsEpsilon(t, e, r, i) {
    r = defaultValue(r, 0), i = defaultValue(i, r);
    const s = Math.abs(t - e);
    return s <= i || s <= r * Math.max(Math.abs(t), Math.abs(e));
  }
  static toRadians(t) {
    return MathUtils.degToRad(t);
  }
  static clamp(t, e, r) {
    return t < e ? e : t > r ? r : t;
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
    if (!defined$2(t))
      throw new DeveloperError("angle is required.");
    return t >= -_CesiumMath.PI && t <= _CesiumMath.PI ? t : _CesiumMath.zeroToTwoPi(t + _CesiumMath.PI) - _CesiumMath.PI;
  }
  static normalize(t, e, r) {
    return 0 === (r = Math.max(r - e, 0)) ? 0 : _CesiumMath.clamp((t - e) / r, 0, 1);
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
  static fromElements(t, e, r) {
    return r || (r = new Vector2()), r.set(t, e), r;
  }
  static lerp(t, e, r, i) {
    return i || (i = new Vector2()), i.lerpVectors(t, e, r), i;
  }
  static equalsEpsilon(t, e, r, i) {
    return t === e || defined$2(t) && defined$2(e) && CesiumMath.equalsEpsilon(t.x, e.x, r, i) && CesiumMath.equalsEpsilon(t.y, e.y, r, i);
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
  static add(t, e, r) {
    return r || (r = new Vector2()), r.addVectors(t, e);
  }
  static multiplyByScalar(t, e, r) {
    return r || (r = new Vector2()), r.copy(t).multiplyScalar(e), r;
  }
  static subtract(t, e, r) {
    return r || (r = new Vector2()), r.subVectors(t, e), r;
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
    return !(!defined$2(t) || !defined$2(e)) && t.equals(e);
  }
  static normalize(t, e) {
    return t === e ? (t.normalize(), t) : (e.copy(t), e.normalize(), e);
  }
  static add(t, e, r) {
    return r || (r = new Vector3$1()), r.addVectors(t, e);
  }
  static dot(t, e) {
    return t.dot(e);
  }
  static cross(t, e, r) {
    return r || (r = new Vector3$1()), r.crossVectors(t, e), r;
  }
  static magnitudeSquared(t) {
    return t.lengthSq();
  }
  static multiplyByScalar(t, e, r) {
    return r || (r = new Vector3$1()), r.copy(t).multiplyScalar(e), r;
  }
  static divideByScalar(t, e, r) {
    return r || (r = new Vector3$1()), r.x = t.x / e, r.y = t.y / e, r.z = t.z / e, r;
  }
  static subtract(t, e, r) {
    return r || (r = new Vector3$1()), r.subVectors(t, e), r;
  }
  static distance(t, e) {
    return t.distanceTo(e);
  }
  static negate(t, e) {
    return e || (e = new Vector3$1()), e.copy(t), e.negate(), e;
  }
  static multiplyComponents(t, e, r) {
    return r || (r = new Vector3$1()), r.multiplyVectors(t, e), r;
  }
  static magnitude(t) {
    return t.length();
  }
  static equalsEpsilon(t, e, r, i) {
    return t === e || defined$2(t) && defined$2(e) && CesiumMath.equalsEpsilon(t.x, e.x, r, i) && CesiumMath.equalsEpsilon(t.y, e.y, r, i) && CesiumMath.equalsEpsilon(t.z, e.z, r, i);
  }
  static fromCartesian4(t, e) {
    return e || (e = new Vector3$1()), e.set(t.x, t.y, t.z), e;
  }
  static fromElements(t, e, r, i) {
    return i || (i = new Vector3$1()), i.set(t, e, r), i;
  }
  static fromRadians(t, e, r, i, s) {
    r = defaultValue(r, 0);
    const n = defined$2(i) ? i.radiiSquared : wgs84RadiiSquared, a = Math.cos(e);
    scratchN$1.x = a * Math.cos(t), scratchN$1.y = a * Math.sin(t), scratchN$1.z = Math.sin(e), scratchN$1 = _Cartesian3.normalize(scratchN$1, scratchN$1), _Cartesian3.multiplyComponents(n, scratchN$1, scratchK$1);
    const o = Math.sqrt(_Cartesian3.dot(scratchN$1, scratchK$1));
    return scratchK$1 = _Cartesian3.divideByScalar(scratchK$1, o, scratchK$1), scratchN$1 = _Cartesian3.multiplyByScalar(scratchN$1, r, scratchN$1), defined$2(s) || (s = new Vector3$1()), _Cartesian3.add(scratchK$1, scratchN$1, s);
  }
  static angleBetween(t, e) {
    _Cartesian3.normalize(t, angleBetweenScratch), _Cartesian3.normalize(e, angleBetweenScratch2);
    const r = _Cartesian3.dot(angleBetweenScratch, angleBetweenScratch2), i = _Cartesian3.magnitude(_Cartesian3.cross(angleBetweenScratch, angleBetweenScratch2, angleBetweenScratch));
    return Math.atan2(i, r);
  }
  static fromDegrees(t, e, r, i, s) {
    return t = CesiumMath.toRadians(t), e = CesiumMath.toRadians(e), _Cartesian3.fromRadians(t, e, r, i, s);
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
  const r = _Cartesian3.normalize(t, mostOrthogonalAxisScratch);
  return _Cartesian3.abs(r, r), e = r.x <= r.y ? r.x <= r.z ? _Cartesian3.clone(_Cartesian3.UNIT_X, e) : _Cartesian3.clone(_Cartesian3.UNIT_Z, e) : r.y <= r.z ? _Cartesian3.clone(_Cartesian3.UNIT_Y, e) : _Cartesian3.clone(_Cartesian3.UNIT_Z, e);
});
class Rectangle {
  constructor(t, e, r, i) {
    this.west = t || 0, this.south = e || 0, this.east = r || 0, this.north = i || 0;
  }
  get width() {
    return Rectangle.computeWidth(this);
  }
  get height() {
    return Rectangle.computeHeight(this);
  }
}
Rectangle.fromDegrees = function(t, e, r, i, s) {
  return t = MathUtils.degToRad(defaultValue(t, 0)), e = MathUtils.degToRad(defaultValue(e, 0)), r = MathUtils.degToRad(defaultValue(r, 0)), i = MathUtils.degToRad(defaultValue(i, 0)), defined$2(s) ? (s.west = t, s.south = e, s.east = r, s.north = i, s) : new Rectangle(t, e, r, i);
}, Rectangle.computeWidth = function(t) {
  let e = t.east;
  const r = t.west;
  return e < r && (e += CesiumMath.TWO_PI), e - r;
}, Rectangle.computeHeight = function(t) {
  return t.north - t.south;
}, Rectangle.clone = function(t, e) {
  if (defined$2(t))
    return defined$2(e) ? (e.west = t.west, e.south = t.south, e.east = t.east, e.north = t.north, e) : new Rectangle(t.west, t.south, t.east, t.north);
}, Rectangle.southwest = function(t, e) {
  return defined$2(e) ? (e.x = t.west, e.y = t.south, e.z = 0, e) : new Vector3$1(t.west, t.south);
}, Rectangle.northeast = function(t, e) {
  return defined$2(e) ? (e.x = t.east, e.y = t.north, e.z = 0, e) : new Vector3$1(t.east, t.north);
}, Rectangle.southeast = function(t, e) {
  return defined$2(e) ? (e.x = t.east, e.y = t.south, e.z = 0, e) : new Vector3$1(t.east, t.south);
}, Rectangle.northwest = function(t, e) {
  return defined$2(e) ? (e.x = t.west, e.y = t.north, e.z = 0, e) : new Vector3$1(t.west, t.north);
}, Rectangle.center = function(t, e) {
  let r = t.east;
  const i = t.west;
  r < i && (r += CesiumMath.TWO_PI);
  const s = CesiumMath.negativePiToPi(0.5 * (i + r)), n = 0.5 * (t.south + t.north);
  return defined$2(e) ? (e.x = s, e.y = n, e.z = 0, e) : new Vector3$1(s, n);
}, Rectangle.contains = function(t, e) {
  let r = e.x;
  const i = e.y, s = t.west;
  let n = t.east;
  return n < s && (n += CesiumMath.TWO_PI, r < 0 && (r += CesiumMath.TWO_PI)), (r > s || CesiumMath.equalsEpsilon(r, s, CesiumMath.EPSILON14)) && (r < n || CesiumMath.equalsEpsilon(r, n, CesiumMath.EPSILON14)) && i >= t.south && i <= t.north;
};
const maxPI = Math.PI + 1e-5, minPI = -Math.PI - 1e-5, maxPIOverTwo = CesiumMath.PI_OVER_TWO + 1e-5, minPIOverTwo = -CesiumMath.PI_OVER_TWO - 1e-5;
Rectangle.fromBox = function(t, e, r = false) {
  const i = t.min, s = t.max;
  let n = i.x / 180 * Math.PI, a = i.y / 180 * Math.PI, o = s.x / 180 * Math.PI, h = s.y / 180 * Math.PI;
  return r && (n < minPI && (n = -Math.PI), n > maxPI && (n = Math.PI), a < minPIOverTwo && (a = -CesiumMath.PI_OVER_TWO), a > maxPIOverTwo && (a = CesiumMath.PI_OVER_TWO), o > maxPI && (o = Math.PI), o < minPI && (o = -Math.PI), h > maxPIOverTwo && (h = CesiumMath.PI_OVER_TWO), h < minPIOverTwo && (h = -CesiumMath.PI_OVER_TWO)), defined$2(e) ? (e.west = n, e.south = a, e.east = o, e.north = h, e) : new Rectangle(n, a, o, h);
}, Rectangle.MAX_VALUE = Object.freeze(new Rectangle(-Math.PI, -CesiumMath.PI_OVER_TWO, Math.PI, CesiumMath.PI_OVER_TWO));
const scaleToGeodeticSurfaceIntersection = new Vector3$1(), scaleToGeodeticSurfaceGradient = new Vector3$1();
function scaleToGeodeticSurface(t, e, r, i, s) {
  const n = t.x, a = t.y, o = t.z, h = e.x, l = e.y, c = e.z, u = n * n * h * h, d = a * a * l * l, p = o * o * c * c, f = u + d + p, _ = Math.sqrt(1 / f), y = scaleToGeodeticSurfaceIntersection.copy(t).multiplyScalar(_);
  if (f < i)
    return s || (s = new Vector3$1()), isFinite(_) ? s.copy(y) : void 0;
  const m = r.x, g = r.y, x = r.z, M = scaleToGeodeticSurfaceGradient;
  M.x = y.x * m * 2, M.y = y.y * g * 2, M.z = y.z * x * 2;
  let w, b, v, S, C, E, P, T, I, z, A2, O = (1 - _) * t.length() / (0.5 * M.length()), N = 0;
  do {
    O -= N, v = 1 / (1 + O * m), S = 1 / (1 + O * g), C = 1 / (1 + O * x), E = v * v, P = S * S, T = C * C, I = E * v, z = P * S, A2 = T * C, w = u * E + d * P + p * T - 1, b = u * I * m + d * z * g + p * A2 * x, N = w / (-2 * b);
  } while (Math.abs(w) > 1e-12);
  return s ? (s.x = n * v, s.y = a * S, s.z = o * C, s) : new Vector3$1(n * v, a * S, o * C);
}
const cartesianToCartographicN = new Vector3$1(), cartesianToCartographicP = new Vector3$1(), cartesianToCartographicH = new Vector3$1(), wgs84OneOverRadii = new Vector3$1(1 / 6378137, 1 / 6378137, 1 / 6356752314245179e-9), wgs84OneOverRadiiSquared = new Vector3$1(1 / 40680631590769, 1 / 40680631590769, 1 / 40408299984661445e-3), wgs84CenterToleranceSquared = CesiumMath.EPSILON1;
const _Cartographic = class {
  static fromRadians(t, e, r, i) {
    return r = defaultValue(r, 0), defined$2(i) ? (i.x = t, i.y = e, i.z = r, i) : new Vector3$1(t, e, r);
  }
  static fromDegrees(t, e, r, i) {
    return t = CesiumMath.toRadians(t), e = CesiumMath.toRadians(e), _Cartographic.fromRadians(t, e, r, i);
  }
  static fromCartesian(t, e, r) {
    const i = defined$2(e) ? e.oneOverRadii : wgs84OneOverRadii, s = defined$2(e) ? e.oneOverRadiiSquared : wgs84OneOverRadiiSquared, n = scaleToGeodeticSurface(t, i, s, defined$2(e) ? e._centerToleranceSquared : wgs84CenterToleranceSquared, cartesianToCartographicP);
    if (!defined$2(n))
      return;
    let a = Cartesian3.multiplyComponents(n, s, cartesianToCartographicN);
    a = Cartesian3.normalize(a, a);
    const o = Cartesian3.subtract(t, n, cartesianToCartographicH), h = Math.atan2(a.y, a.x), l = Math.asin(a.z), c = CesiumMath.sign(Cartesian3.dot(o, t)) * Cartesian3.magnitude(o);
    return defined$2(r) ? (r.x = h, r.y = l, r.z = c, r) : new Vector3$1(h, l, c);
  }
  static toCartesian(t, e, r) {
    return Cartesian3.fromRadians(t.x, t.y, t.z, e, r);
  }
  static clone(t, e) {
    if (defined$2(t))
      return defined$2(e) ? (e.x = t.x, e.y = t.y, e.z = t.z, e) : new Vector3$1(t.x, t.y, t.z);
  }
  static equals(t, e) {
    return t === e || defined$2(t) && defined$2(e) && t.x === e.x && t.y === e.y && t.z === e.z;
  }
  static equalsEpsilon(t, e, r) {
    return r = defaultValue(r, 0), t === e || defined$2(t) && defined$2(e) && CesiumMath.equalsEpsilon(t.x, e.x, r) && CesiumMath.equalsEpsilon(t.y, e.y, r) && CesiumMath.equalsEpsilon(t.z, e.z, r);
  }
};
let Cartographic = _Cartographic;
__publicField(Cartographic, "fromRadians", function(t, e, r, i) {
  return r = defaultValue(r, 0), defined$2(i) ? (i.x = t, i.y = e, i.z = r, i) : new Vector3$1(t, e, r);
});
__publicField(Cartographic, "fromDegrees", function(t, e, r, i) {
  return t = CesiumMath.toRadians(t), e = CesiumMath.toRadians(e), _Cartographic.fromRadians(t, e, r, i);
});
__publicField(Cartographic, "ZERO", Object.freeze(new Vector3$1(0, 0, 0)));
const _inputVector3 = new Vector3$1(), _outputVector3 = new Vector3$1();
class Ellipsoid {
  constructor(t, e, r) {
    this._radii = new Vector3$1(t, e, r), this._radiiSquared = new Vector3$1(t * t, e * e, r * r), this._radiiToTheFourth = new Vector3$1(t * t * t * t, e * e * e * e, r * r * r * r), this._oneOverRadii = new Vector3$1(0 === t ? 0 : 1 / t, 0 === e ? 0 : 1 / e, 0 === r ? 0 : 1 / r), this._oneOverRadiiSquared = new Vector3$1(0 === t ? 0 : 1 / (t * t), 0 === e ? 0 : 1 / (e * e), 0 === r ? 0 : 1 / (r * r)), this._minimumRadius = Math.min(t, e, r), this._maximumRadius = Math.max(t, e, r), this._centerToleranceSquared = 0.1, 0 !== this._radiiSquared.z && (this._squaredXOverSquaredZ = this._radiiSquared.x / this._radiiSquared.z);
  }
  static fromCartesian3(t) {
    return new Ellipsoid(t.x, t.y, t.z);
  }
  geodeticSurfaceNormalCartographic(t, e) {
    e || (e = new Vector3$1());
    const r = t.x, i = t.y, s = Math.cos(i), n = s * Math.cos(r), a = s * Math.sin(r), o = Math.sin(i);
    return e.set(n, a, o), e.normalize(), e;
  }
  cartographicDegreeToCartesian(t, e) {
    return _inputVector3.set(MathUtils.degToRad(t.x), MathUtils.degToRad(t.y), t.z), this.cartographicToCartesian(_inputVector3, e);
  }
  cartographicToCartesian(t, e) {
    const r = this.geodeticSurfaceNormalCartographic(t);
    e || (e = new Vector3$1()), e.multiplyVectors(this._radiiSquared, r);
    const i = Math.sqrt(r.clone().dot(e));
    return e.divideScalar(i), r.multiplyScalar(t.z), e.add(r), e;
  }
  cartesianToCartographicDegree(t, e) {
    const r = this.cartesianToCartographic(t, e);
    if (r)
      return (e = r).x = MathUtils.radToDeg(e.x), e.y = MathUtils.radToDeg(e.y), e;
  }
  scaleToGeodeticSurface(t, e) {
    return scaleToGeodeticSurface(t, this._oneOverRadii, this._oneOverRadiiSquared, this._centerToleranceSquared, e);
  }
  scaleToGeocentricSurface(t, e) {
    e || (e = new Vector3$1());
    const r = t.x, i = t.y, s = t.z, n = this._oneOverRadiiSquared, a = 1 / Math.sqrt(r * r * n.x + i * i * n.y + s * s * n.z);
    return e.copy(t).multiplyScalar(a);
  }
  cartesianToCartographic(t, e) {
    const r = this.scaleToGeodeticSurface(t, _outputVector3);
    if (!r)
      return;
    const i = this.geodeticSurfaceNormal(r), s = t.clone();
    s.sub(r);
    const n = Math.atan2(i.y, i.x), a = Math.asin(i.z), o = Math.sign(s.dot(t)) * s.length();
    return e || (e = new Vector3$1()), e.set(n, a, o), e;
  }
  geodeticSurfaceNormal(t, e) {
    return defined$2(e) || (e = new Vector3$1()), e.multiplyVectors(t, this._oneOverRadiiSquared), e.normalize(), e;
  }
  getSurfaceNormalIntersectionWithZAxis(t, e, r) {
    e = defaultValue(e, 0);
    const i = this._squaredXOverSquaredZ;
    if (defined$2(r) || (r = new Vector3$1()), r.x = 0, r.y = 0, r.z = t.z * (1 - i), !(Math.abs(r.z) >= this._radii.z - e))
      return r;
  }
  transformPositionToScaledSpace(t, e) {
    return Cartesian3.multiplyComponents(t, this._oneOverRadii, e);
  }
  static clone(t, e) {
    if (!t)
      return;
    const r = t._radii;
    return e ? (Cartesian3.clone(r, e._radii), Cartesian3.clone(t._radiiSquared, e._radiiSquared), Cartesian3.clone(t._radiiToTheFourth, e._radiiToTheFourth), Cartesian3.clone(t._oneOverRadii, e._oneOverRadii), Cartesian3.clone(t._oneOverRadiiSquared, e._oneOverRadiiSquared), e._minimumRadius = t._minimumRadius, e._maximumRadius = t._maximumRadius, e._centerToleranceSquared = t._centerToleranceSquared, e) : new Ellipsoid(r.x, r.y, r.z);
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
  static fromAxisAngle(t, e, r) {
    return r || (r = new Quaternion$1()), fromAxisAngleScratch.copy(t), fromAxisAngleScratch.normalize(), r.setFromAxisAngle(fromAxisAngleScratch, e), r;
  }
  static multiply(t, e, r) {
    return r || (r = new Quaternion$1()), r.multiplyQuaternions(t, e), r;
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
  static multiplyByPoint(t, e, r) {
    const i = t.elements, s = e.x, n = e.y, a = e.z, o = i[0] * s + i[4] * n + i[8] * a + i[12], h = i[1] * s + i[5] * n + i[9] * a + i[13], l = i[2] * s + i[6] * n + i[10] * a + i[14];
    return r.x = o, r.y = h, r.z = l, r;
  }
  static multiplyByPointAsVector(t, e, r) {
    const i = t.elements, s = e.x, n = e.y, a = e.z, o = i[0] * s + i[4] * n + i[8] * a, h = i[1] * s + i[5] * n + i[9] * a, l = i[2] * s + i[6] * n + i[10] * a;
    return r.x = o, r.y = h, r.z = l, r;
  }
  static computeViewportTransformation(t, e, r, i) {
    defined$2(i) || (i = new Matrix4()), t = defaultValue(t, defaultValue.EMPTY_OBJECT);
    const s = defaultValue(t.x, 0), n = defaultValue(t.y, 0), a = defaultValue(t.width, 0), o = defaultValue(t.height, 0);
    e = defaultValue(e, 0);
    const h = 0.5 * a, l = 0.5 * o, c = 0.5 * ((r = defaultValue(r, 1)) - e), u = h, d = l, p = c, f = s + h, _ = n + l, y = e + c, m = i.elements;
    return m[0] = u, m[1] = 0, m[2] = 0, m[3] = 0, m[4] = 0, m[5] = d, m[6] = 0, m[7] = 0, m[8] = 0, m[9] = 0, m[10] = p, m[11] = 0, m[12] = f, m[13] = _, m[14] = y, m[15] = 1, i;
  }
  static equals(t, e) {
    return t.equals(e);
  }
  static multiplyByVector(t, e, r) {
    return r || (r = new Vector4()), r.copy(e), r.applyMatrix4(t), r;
  }
  static getColumn(t, e, r) {
    const i = t.elements, s = 4 * e, n = i[s], a = i[s + 1], o = i[s + 2], h = i[s + 3];
    return r.x = n, r.y = a, r.z = o, r.w = h, r;
  }
  static fromTranslationQuaternionRotationScale(t, e, r, i) {
    i || (i = new Matrix4());
    const s = r.x, n = r.y, a = r.z, o = e.x * e.x, h = e.x * e.y, l = e.x * e.z, c = e.x * e.w, u = e.y * e.y, d = e.y * e.z, p = e.y * e.w, f = e.z * e.z, _ = e.z * e.w, y = e.w * e.w, m = o - u - f + y, g = 2 * (h - _), x = 2 * (l + p), M = 2 * (h + _), w = -o + u - f + y, b = 2 * (d - c), v = 2 * (l - p), S = 2 * (d + c), C = -o - u + f + y, E = i.elements;
    return E[0] = m * s, E[1] = M * s, E[2] = v * s, E[3] = 0, E[4] = g * n, E[5] = w * n, E[6] = S * n, E[7] = 0, E[8] = x * a, E[9] = b * a, E[10] = C * a, E[11] = 0, E[12] = t.x, E[13] = t.y, E[14] = t.z, E[15] = 1, i;
  }
}
__publicField(StaticMatrix4, "IDENTITY", Object.freeze(new Matrix4()));
StaticMatrix4.ZERO = Object.freeze(new Matrix4(0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0));
const Transforms = {}, scratchHPRQuaternion = new Quaternion$1(), scratchScale$1 = new Vector3$1(1, 1, 1), scratchHPRMatrix4 = new Matrix4(), vectorProductLocalFrame = { up: { south: "east", north: "west", west: "south", east: "north" }, down: { south: "west", north: "east", west: "north", east: "south" }, south: { up: "west", down: "east", west: "down", east: "up" }, north: { up: "east", down: "west", west: "up", east: "down" }, west: { up: "north", down: "south", north: "down", south: "up" }, east: { up: "south", down: "north", north: "up", south: "down" } };
let degeneratePositionLocalFrame = { north: [-1, 0, 0], east: [0, 1, 0], up: [0, 0, 1], south: [1, 0, 0], west: [0, -1, 0], down: [0, 0, -1] }, localFrameToFixedFrameCache = {}, scratchCalculateCartesian = { east: new Vector3$1(), north: new Vector3$1(), up: new Vector3$1(), west: new Vector3$1(), south: new Vector3$1(), down: new Vector3$1() }, scratchFirstCartesian = new Vector3$1(), scratchSecondCartesian = new Vector3$1(), scratchThirdCartesian = new Vector3$1();
const defined$1 = (t) => void 0 !== t, zeroVector3 = new Vector3$1(), mathSign = (t) => 0 === (t = +t) ? t : t > 0 ? 1 : -1, scratchN = new Vector3$1(), scratchK = new Vector3$1(), radianToEcef = function(t, e, r = 0, i) {
  const s = new Vector3$1(40680631590769, 40680631590769, 40408299984661445e-3), n = Math.cos(e);
  scratchN.x = n * Math.cos(t), scratchN.y = n * Math.sin(t), scratchN.z = Math.sin(e), scratchN.normalize(), scratchK.multiplyVectors(s, scratchN);
  const a = Math.sqrt(scratchN.dot(scratchK));
  return scratchK.divideScalar(a), scratchN.multiplyScalar(r), defined$1(i) || (i = new Vector3$1()), i.addVectors(scratchK, scratchN);
}, lnglatToEcef = (t, e, r = 0, i) => radianToEcef(t * Math.PI / 180, e * Math.PI / 180, r, i);
function Interval(t, e) {
  this.start = defaultValue(t, 0), this.stop = defaultValue(e, 0);
}
Transforms.lnglatToEcef = lnglatToEcef, Transforms.radianToEcef = radianToEcef, Transforms.localFrameToFixedFrameGenerator = function(t, e) {
  if (!vectorProductLocalFrame.hasOwnProperty(t) || !vectorProductLocalFrame[t].hasOwnProperty(e))
    throw new Error("firstAxis and secondAxis must be east, north, up, west, south or down.");
  let r, i = vectorProductLocalFrame[t][e], s = t + e;
  return defined$1(localFrameToFixedFrameCache[s]) ? r = localFrameToFixedFrameCache[s] : (r = function(r2, s2, n) {
    if (!defined$1(r2))
      throw new Error("origin is required.");
    if (defined$1(n) || (n = new Matrix4()), r2.equals(zeroVector3))
      scratchFirstCartesian.fromArray(degeneratePositionLocalFrame[t]), scratchSecondCartesian.fromArray(degeneratePositionLocalFrame[e]), scratchThirdCartesian.fromArray(degeneratePositionLocalFrame[i]);
    else if (Math.abs(r2.x) < 1e-14 && Math.abs(r2.y) < 1e-14) {
      let s3 = mathSign(r2.z);
      scratchFirstCartesian.fromArray(degeneratePositionLocalFrame[t]), "east" !== t && "west" !== t && scratchFirstCartesian.multiplyScalar(s3), scratchSecondCartesian.fromArray(degeneratePositionLocalFrame[e]), "east" !== e && "west" !== e && scratchSecondCartesian.multiplyScalar(s3), scratchThirdCartesian.fromArray(degeneratePositionLocalFrame[i]), "east" !== i && "west" !== i && scratchThirdCartesian.multiplyScalar(s3);
    } else {
      (s2 = s2 || Ellipsoid.WGS84).geodeticSurfaceNormal(r2, scratchCalculateCartesian.up);
      let n2 = scratchCalculateCartesian.up, a2 = scratchCalculateCartesian.east;
      a2.x = -r2.y, a2.y = r2.x, a2.z = 0, scratchCalculateCartesian.east.copy(a2).normalize(), scratchCalculateCartesian.north.crossVectors(n2, a2), scratchCalculateCartesian.down.copy(scratchCalculateCartesian.up).multiplyScalar(-1), scratchCalculateCartesian.west.copy(scratchCalculateCartesian.east).multiplyScalar(-1), scratchCalculateCartesian.south.copy(scratchCalculateCartesian.north).multiplyScalar(-1), scratchFirstCartesian = scratchCalculateCartesian[t], scratchSecondCartesian = scratchCalculateCartesian[e], scratchThirdCartesian = scratchCalculateCartesian[i];
    }
    const a = n.elements;
    return a[0] = scratchFirstCartesian.x, a[1] = scratchFirstCartesian.y, a[2] = scratchFirstCartesian.z, a[3] = 0, a[4] = scratchSecondCartesian.x, a[5] = scratchSecondCartesian.y, a[6] = scratchSecondCartesian.z, a[7] = 0, a[8] = scratchThirdCartesian.x, a[9] = scratchThirdCartesian.y, a[10] = scratchThirdCartesian.z, a[11] = 0, a[12] = r2.x, a[13] = r2.y, a[14] = r2.z, a[15] = 1, n;
  }, localFrameToFixedFrameCache[s] = r), r;
}, Transforms.eastNorthUpToFixedFrame = Transforms.localFrameToFixedFrameGenerator("east", "north"), Transforms.headingPitchRollToFixedFrame = function(t, e, r, i, s) {
  i = i || Transforms.eastNorthUpToFixedFrame;
  const n = StaticQuaternion.fromHeadingPitchRoll(e, scratchHPRQuaternion), a = StaticMatrix4.fromTranslationQuaternionRotationScale(Cartesian3.ZERO, n, scratchScale$1, scratchHPRMatrix4);
  return (s = i(t, r, s)).multiply(a);
}, Transforms.northEastDownToFixedFrame = Transforms.localFrameToFixedFrameGenerator("north", "east"), Transforms.northUpEastToFixedFrame = Transforms.localFrameToFixedFrameGenerator("north", "up"), Transforms.northWestUpToFixedFrame = Transforms.localFrameToFixedFrameGenerator("north", "west");
class StaticMatrix3 {
  static fromQuaternion(t, e) {
    const r = t.x * t.x, i = t.x * t.y, s = t.x * t.z, n = t.x * t.w, a = t.y * t.y, o = t.y * t.z, h = t.y * t.w, l = t.z * t.z, c = t.z * t.w, u = t.w * t.w, d = r - a - l + u, p = 2 * (i - c), f = 2 * (s + h), _ = 2 * (i + c), y = -r + a - l + u, m = 2 * (o - n), g = 2 * (s - h), x = 2 * (o + n), M = -r - a + l + u;
    return e || (e = new Matrix3()), e.set(d, p, f, _, y, m, g, x, M), e;
  }
  static getColumn(t, e, r) {
    const i = t.elements, s = 3 * e, n = i[s], a = i[s + 1], o = i[s + 2];
    return r.x = n, r.y = a, r.z = o, r;
  }
  static multiplyByVector(t, e, r) {
    return r || (r = new Vector3$1()), r.copy(e), r.applyMatrix3(t), r;
  }
  static multiplyByScale(t, e, r) {
    r || (r = new Matrix3());
    const i = r.elements, s = t.elements;
    return i[0] = s[0] * e.x, i[1] = s[1] * e.x, i[2] = s[2] * e.x, i[3] = s[3] * e.y, i[4] = s[4] * e.y, i[5] = s[5] * e.y, i[6] = s[6] * e.z, i[7] = s[7] * e.z, i[8] = s[8] * e.z, r;
  }
  static transpose(t, e) {
    return e || (e = new Matrix3()), e.copy(t).transpose(), e;
  }
  static fromScale(t, e) {
    e || (e = new Matrix3());
    const r = e.elements;
    return r[0] = t.x, r[1] = 0, r[2] = 0, r[3] = 0, r[4] = t.y, r[5] = 0, r[6] = 0, r[7] = 0, r[8] = t.z, e;
  }
  static multiply(t, e, r) {
    r || (r = new Matrix3());
    const i = t.elements, s = e.elements, n = r.elements, a = i[0], o = i[3], h = i[6], l = i[1], c = i[4], u = i[7], d = i[2], p = i[5], f = i[8], _ = s[0], y = s[3], m = s[6], g = s[1], x = s[4], M = s[7], w = s[2], b = s[5], v = s[8];
    return n[0] = a * _ + o * g + h * w, n[3] = a * y + o * x + h * b, n[6] = a * m + o * M + h * v, n[1] = l * _ + c * g + u * w, n[4] = l * y + c * x + u * b, n[7] = l * m + c * M + u * v, n[2] = d * _ + p * g + f * w, n[5] = d * y + p * x + f * b, n[8] = d * m + p * M + f * v, r;
  }
  static clone(t, e) {
    if (defined$2(t))
      return defined$2(e) ? (e.clone(t), e) : new Matrix3(t[0], t[3], t[6], t[1], t[4], t[7], t[2], t[5], t[8]);
  }
  static setColumn(t, e, r, i) {
    const s = (i = StaticMatrix3.clone(t, i)).elements, n = 3 * e;
    return s[n] = r.x, s[n + 1] = r.y, s[n + 2] = r.z, i;
  }
}
StaticMatrix3.ZERO = Matrix3.ZERO = Object.freeze(new Matrix3(0, 0, 0, 0, 0, 0, 0, 0, 0)), StaticMatrix3.COLUMN0ROW0 = 0, StaticMatrix3.COLUMN0ROW1 = 1, StaticMatrix3.COLUMN0ROW2 = 2, StaticMatrix3.COLUMN1ROW0 = 3, StaticMatrix3.COLUMN1ROW1 = 4, StaticMatrix3.COLUMN1ROW2 = 5, StaticMatrix3.COLUMN2ROW0 = 6, StaticMatrix3.COLUMN2ROW1 = 7, StaticMatrix3.COLUMN2ROW2 = 8;
var QuadraticRealPolynomial = {};
function addWithCancellationCheck$1(t, e, r) {
  var i = t + e;
  return CesiumMath.sign(t) !== CesiumMath.sign(e) && Math.abs(i / Math.max(Math.abs(t), Math.abs(e))) < r ? 0 : i;
}
QuadraticRealPolynomial.computeDiscriminant = function(t, e, r) {
  if ("number" != typeof t)
    throw new DeveloperError("a is a required number.");
  if ("number" != typeof e)
    throw new DeveloperError("b is a required number.");
  if ("number" != typeof r)
    throw new DeveloperError("c is a required number.");
  return e * e - 4 * t * r;
}, QuadraticRealPolynomial.computeRealRoots = function(t, e, r) {
  if ("number" != typeof t)
    throw new DeveloperError("a is a required number.");
  if ("number" != typeof e)
    throw new DeveloperError("b is a required number.");
  if ("number" != typeof r)
    throw new DeveloperError("c is a required number.");
  var i;
  if (0 === t)
    return 0 === e ? [] : [-r / e];
  if (0 === e) {
    if (0 === r)
      return [0, 0];
    var s = Math.abs(r), n = Math.abs(t);
    if (s < n && s / n < CesiumMath.EPSILON14)
      return [0, 0];
    if (s > n && n / s < CesiumMath.EPSILON14)
      return [];
    if ((i = -r / t) < 0)
      return [];
    var a = Math.sqrt(i);
    return [-a, a];
  }
  if (0 === r)
    return (i = -e / t) < 0 ? [i, 0] : [0, i];
  var o = addWithCancellationCheck$1(e * e, -(4 * t * r), CesiumMath.EPSILON14);
  if (o < 0)
    return [];
  var h = -0.5 * addWithCancellationCheck$1(e, CesiumMath.sign(e) * Math.sqrt(o), CesiumMath.EPSILON14);
  return e > 0 ? [h / t, r / h] : [r / h, h / t];
};
var CubicRealPolynomial = {};
function computeRealRoots(t, e, r, i) {
  var s, n, a = t, o = e / 3, h = r / 3, l = i, c = a * h, u = o * l, d = o * o, p = h * h, f = a * h - d, _ = a * l - o * h, y = o * l - p, m = 4 * f * y - _ * _;
  if (m < 0) {
    var g, x, M;
    d * u >= c * p ? (g = a, x = f, M = -2 * o * f + a * _) : (g = l, x = y, M = -l * _ + 2 * h * y);
    var w = -(M < 0 ? -1 : 1) * Math.abs(g) * Math.sqrt(-m), b = (n = -M + w) / 2, v = b < 0 ? -Math.pow(-b, 1 / 3) : Math.pow(b, 1 / 3), S = n === w ? -v : -x / v;
    return s = x <= 0 ? v + S : -M / (v * v + S * S + x), d * u >= c * p ? [(s - o) / a] : [-l / (s + h)];
  }
  var C = f, E = -2 * o * f + a * _, P = y, T = -l * _ + 2 * h * y, I = Math.sqrt(m), z = Math.sqrt(3) / 2, A2 = Math.abs(Math.atan2(a * I, -E) / 3);
  s = 2 * Math.sqrt(-C);
  var O = Math.cos(A2);
  n = s * O;
  var N = s * (-O / 2 - z * Math.sin(A2)), R = n + N > 2 * o ? n - o : N - o, $ = a, V = R / $;
  A2 = Math.abs(Math.atan2(l * I, -T) / 3);
  var G = -l, k = (n = (s = 2 * Math.sqrt(-P)) * (O = Math.cos(A2))) + (N = s * (-O / 2 - z * Math.sin(A2))) < 2 * h ? n + h : N + h, q = G / k, B = -R * k - $ * G, F = (h * B - o * (R * G)) / (-o * B + h * ($ * k));
  return V <= F ? V <= q ? F <= q ? [V, F, q] : [V, q, F] : [q, V, F] : V <= q ? [F, V, q] : F <= q ? [F, q, V] : [q, F, V];
}
CubicRealPolynomial.computeDiscriminant = function(t, e, r, i) {
  if ("number" != typeof t)
    throw new DeveloperError("a is a required number.");
  if ("number" != typeof e)
    throw new DeveloperError("b is a required number.");
  if ("number" != typeof r)
    throw new DeveloperError("c is a required number.");
  if ("number" != typeof i)
    throw new DeveloperError("d is a required number.");
  var s = e * e, n = r * r;
  return 18 * t * e * r * i + s * n - 27 * (t * t) * (i * i) - 4 * (t * n * r + s * e * i);
}, CubicRealPolynomial.computeRealRoots = function(t, e, r, i) {
  if ("number" != typeof t)
    throw new DeveloperError("a is a required number.");
  if ("number" != typeof e)
    throw new DeveloperError("b is a required number.");
  if ("number" != typeof r)
    throw new DeveloperError("c is a required number.");
  if ("number" != typeof i)
    throw new DeveloperError("d is a required number.");
  var s, n;
  if (0 === t)
    return QuadraticRealPolynomial.computeRealRoots(e, r, i);
  if (0 === e) {
    if (0 === r) {
      if (0 === i)
        return [0, 0, 0];
      var a = (n = -i / t) < 0 ? -Math.pow(-n, 1 / 3) : Math.pow(n, 1 / 3);
      return [a, a, a];
    }
    return 0 === i ? 0 === (s = QuadraticRealPolynomial.computeRealRoots(t, 0, r)).Length ? [0] : [s[0], 0, s[1]] : computeRealRoots(t, 0, r, i);
  }
  return 0 === r ? 0 === i ? (n = -e / t) < 0 ? [n, 0, 0] : [0, 0, n] : computeRealRoots(t, e, 0, i) : 0 === i ? 0 === (s = QuadraticRealPolynomial.computeRealRoots(t, e, r)).length ? [0] : s[1] <= 0 ? [s[0], s[1], 0] : s[0] >= 0 ? [0, s[0], s[1]] : [s[0], 0, s[1]] : computeRealRoots(t, e, r, i);
};
var QuarticRealPolynomial = {};
function original(t, e, r, i) {
  var s = t * t, n = e - 3 * s / 8, a = r - e * t / 2 + s * t / 8, o = i - r * t / 4 + e * s / 16 - 3 * s * s / 256, h = CubicRealPolynomial.computeRealRoots(1, 2 * n, n * n - 4 * o, -a * a);
  if (h.length > 0) {
    var l = -t / 4, c = h[h.length - 1];
    if (Math.abs(c) < CesiumMath.EPSILON14) {
      var u = QuadraticRealPolynomial.computeRealRoots(1, n, o);
      if (2 === u.length) {
        var d, p = u[0], f = u[1];
        if (p >= 0 && f >= 0) {
          var _ = Math.sqrt(p), y = Math.sqrt(f);
          return [l - y, l - _, l + _, l + y];
        }
        if (p >= 0 && f < 0)
          return [l - (d = Math.sqrt(p)), l + d];
        if (p < 0 && f >= 0)
          return [l - (d = Math.sqrt(f)), l + d];
      }
      return [];
    }
    if (c > 0) {
      var m = Math.sqrt(c), g = (n + c - a / m) / 2, x = (n + c + a / m) / 2, M = QuadraticRealPolynomial.computeRealRoots(1, m, g), w = QuadraticRealPolynomial.computeRealRoots(1, -m, x);
      return 0 !== M.length ? (M[0] += l, M[1] += l, 0 !== w.length ? (w[0] += l, w[1] += l, M[1] <= w[0] ? [M[0], M[1], w[0], w[1]] : w[1] <= M[0] ? [w[0], w[1], M[0], M[1]] : M[0] >= w[0] && M[1] <= w[1] ? [w[0], M[0], M[1], w[1]] : w[0] >= M[0] && w[1] <= M[1] ? [M[0], w[0], w[1], M[1]] : M[0] > w[0] && M[0] < w[1] ? [w[0], M[0], w[1], M[1]] : [M[0], w[0], M[1], w[1]]) : M) : 0 !== w.length ? (w[0] += l, w[1] += l, w) : [];
    }
  }
  return [];
}
function neumark(t, e, r, i) {
  var s = t * t, n = -2 * e, a = r * t + e * e - 4 * i, o = s * i - r * e * t + r * r, h = CubicRealPolynomial.computeRealRoots(1, n, a, o);
  if (h.length > 0) {
    var l, c, u, d, p, f, _ = h[0], y = e - _, m = y * y, g = t / 2, x = y / 2, M = m - 4 * i, w = m + 4 * Math.abs(i), b = s - 4 * _, v = s + 4 * Math.abs(_);
    if (_ < 0 || M * v < b * w) {
      var S = Math.sqrt(b);
      l = S / 2, c = 0 === S ? 0 : (t * x - r) / S;
    } else {
      var C = Math.sqrt(M);
      l = 0 === C ? 0 : (t * x - r) / C, c = C / 2;
    }
    0 === g && 0 === l ? (u = 0, d = 0) : CesiumMath.sign(g) === CesiumMath.sign(l) ? d = _ / (u = g + l) : u = _ / (d = g - l), 0 === x && 0 === c ? (p = 0, f = 0) : CesiumMath.sign(x) === CesiumMath.sign(c) ? f = i / (p = x + c) : p = i / (f = x - c);
    var E = QuadraticRealPolynomial.computeRealRoots(1, u, p), P = QuadraticRealPolynomial.computeRealRoots(1, d, f);
    if (0 !== E.length)
      return 0 !== P.length ? E[1] <= P[0] ? [E[0], E[1], P[0], P[1]] : P[1] <= E[0] ? [P[0], P[1], E[0], E[1]] : E[0] >= P[0] && E[1] <= P[1] ? [P[0], E[0], E[1], P[1]] : P[0] >= E[0] && P[1] <= E[1] ? [E[0], P[0], P[1], E[1]] : E[0] > P[0] && E[0] < P[1] ? [P[0], E[0], P[1], E[1]] : [E[0], P[0], E[1], P[1]] : E;
    if (0 !== P.length)
      return P;
  }
  return [];
}
QuarticRealPolynomial.computeDiscriminant = function(t, e, r, i, s) {
  if ("number" != typeof t)
    throw new DeveloperError("a is a required number.");
  if ("number" != typeof e)
    throw new DeveloperError("b is a required number.");
  if ("number" != typeof r)
    throw new DeveloperError("c is a required number.");
  if ("number" != typeof i)
    throw new DeveloperError("d is a required number.");
  if ("number" != typeof s)
    throw new DeveloperError("e is a required number.");
  var n = t * t, a = e * e, o = a * e, h = r * r, l = h * r, c = i * i, u = c * i, d = s * s;
  return a * h * c - 4 * o * u - 4 * t * l * c + 18 * t * e * r * u - 27 * n * c * c + 256 * (n * t) * (d * s) + s * (18 * o * r * i - 4 * a * l + 16 * t * h * h - 80 * t * e * h * i - 6 * t * a * c + 144 * n * r * c) + d * (144 * t * a * r - 27 * a * a - 128 * n * h - 192 * n * e * i);
}, QuarticRealPolynomial.computeRealRoots = function(t, e, r, i, s) {
  if ("number" != typeof t)
    throw new DeveloperError("a is a required number.");
  if ("number" != typeof e)
    throw new DeveloperError("b is a required number.");
  if ("number" != typeof r)
    throw new DeveloperError("c is a required number.");
  if ("number" != typeof i)
    throw new DeveloperError("d is a required number.");
  if ("number" != typeof s)
    throw new DeveloperError("e is a required number.");
  if (Math.abs(t) < CesiumMath.EPSILON15)
    return CubicRealPolynomial.computeRealRoots(e, r, i, s);
  var n = e / t, a = r / t, o = i / t, h = s / t, l = n < 0 ? 1 : 0;
  switch (l += a < 0 ? l + 1 : l, l += o < 0 ? l + 1 : l, l += h < 0 ? l + 1 : l) {
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
      return original(n, a, o, h);
    case 1:
    case 2:
    case 5:
    case 8:
    case 11:
      return neumark(n, a, o, h);
    default:
      return;
  }
};
var IntersectionTests = { rayPlane: function(t, e, r) {
  if (!defined$2(t))
    throw new DeveloperError("ray is required.");
  if (!defined$2(e))
    throw new DeveloperError("plane is required.");
  defined$2(r) || (r = new Vector3$1());
  var i = t.origin, s = t.direction, n = e.normal, a = Cartesian3.dot(n, s);
  if (!(Math.abs(a) < CesiumMath.EPSILON15)) {
    var o = (-e.constant - Cartesian3.dot(n, i)) / a;
    if (!(o < 0))
      return r = Cartesian3.multiplyByScalar(s, o, r), Cartesian3.add(i, r, r);
  }
} }, scratchEdge0 = new Vector3$1(), scratchEdge1 = new Vector3$1(), scratchPVec = new Vector3$1(), scratchTVec = new Vector3$1(), scratchQVec = new Vector3$1();
IntersectionTests.rayTriangleParametric = function(t, e, r, i, s) {
  if (!defined$2(t))
    throw new DeveloperError("ray is required.");
  if (!defined$2(e))
    throw new DeveloperError("p0 is required.");
  if (!defined$2(r))
    throw new DeveloperError("p1 is required.");
  if (!defined$2(i))
    throw new DeveloperError("p2 is required.");
  s = defaultValue(s, false);
  var n, a, o, h, l, c = t.origin, u = t.direction, d = Cartesian3.subtract(r, e, scratchEdge0), p = Cartesian3.subtract(i, e, scratchEdge1), f = Cartesian3.cross(u, p, scratchPVec), _ = Cartesian3.dot(d, f);
  if (s) {
    if (_ < CesiumMath.EPSILON6)
      return;
    if (n = Cartesian3.subtract(c, e, scratchTVec), (o = Cartesian3.dot(n, f)) < 0 || o > _)
      return;
    if (a = Cartesian3.cross(n, d, scratchQVec), (h = Cartesian3.dot(u, a)) < 0 || o + h > _)
      return;
    l = Cartesian3.dot(p, a) / _;
  } else {
    if (Math.abs(_) < CesiumMath.EPSILON6)
      return;
    var y = 1 / _;
    if (n = Cartesian3.subtract(c, e, scratchTVec), (o = Cartesian3.dot(n, f) * y) < 0 || o > 1)
      return;
    if (a = Cartesian3.cross(n, d, scratchQVec), (h = Cartesian3.dot(u, a) * y) < 0 || o + h > 1)
      return;
    l = Cartesian3.dot(p, a) * y;
  }
  return l;
}, IntersectionTests.rayTriangle = function(t, e, r, i, s, n) {
  var a = IntersectionTests.rayTriangleParametric(t, e, r, i, s);
  if (defined$2(a) && !(a < 0))
    return defined$2(n) || (n = new Vector3$1()), Cartesian3.multiplyByScalar(t.direction, a, n), Cartesian3.add(t.origin, n, n);
};
var scratchLineSegmentTriangleRay = new Ray();
function solveQuadratic(t, e, r, i) {
  var s = e * e - 4 * t * r;
  if (!(s < 0)) {
    if (s > 0) {
      var n = 1 / (2 * t), a = Math.sqrt(s), o = (-e + a) * n, h = (-e - a) * n;
      return o < h ? (i.root0 = o, i.root1 = h) : (i.root0 = h, i.root1 = o), i;
    }
    var l = -e / (2 * t);
    if (0 !== l)
      return i.root0 = i.root1 = l, i;
  }
}
IntersectionTests.lineSegmentTriangle = function(t, e, r, i, s, n, a) {
  if (!defined$2(t))
    throw new DeveloperError("v0 is required.");
  if (!defined$2(e))
    throw new DeveloperError("v1 is required.");
  if (!defined$2(r))
    throw new DeveloperError("p0 is required.");
  if (!defined$2(i))
    throw new DeveloperError("p1 is required.");
  if (!defined$2(s))
    throw new DeveloperError("p2 is required.");
  var o = scratchLineSegmentTriangleRay;
  Cartesian3.clone(t, o.origin), Cartesian3.subtract(e, t, o.direction), Cartesian3.normalize(o.direction, o.direction);
  var h = IntersectionTests.rayTriangleParametric(o, r, i, s, n);
  if (!(!defined$2(h) || h < 0 || h > Cartesian3.distance(t, e)))
    return defined$2(a) || (a = new Vector3$1()), Cartesian3.multiplyByScalar(o.direction, h, a), Cartesian3.add(o.origin, a, a);
};
var raySphereRoots = { root0: 0, root1: 0 };
function raySphere(t, e, r) {
  defined$2(r) || (r = new Interval());
  var i = t.origin, s = t.direction, n = e.center, a = e.radius * e.radius, o = Cartesian3.subtract(i, n, scratchPVec), h = solveQuadratic(Cartesian3.dot(s, s), 2 * Cartesian3.dot(s, o), Cartesian3.magnitudeSquared(o) - a, raySphereRoots);
  if (defined$2(h))
    return r.start = h.root0, r.stop = h.root1, r;
}
IntersectionTests.raySphere = function(t, e, r) {
  if (!defined$2(t))
    throw new DeveloperError("ray is required.");
  if (!defined$2(e))
    throw new DeveloperError("sphere is required.");
  if (defined$2(r = raySphere(t, e, r)) && !(r.stop < 0))
    return r.start = Math.max(r.start, 0), r;
};
var scratchLineSegmentRay = new Ray();
IntersectionTests.lineSegmentSphere = function(t, e, r, i) {
  if (!defined$2(t))
    throw new DeveloperError("p0 is required.");
  if (!defined$2(e))
    throw new DeveloperError("p1 is required.");
  if (!defined$2(r))
    throw new DeveloperError("sphere is required.");
  var s = scratchLineSegmentRay;
  Cartesian3.clone(t, s.origin);
  var n = Cartesian3.subtract(e, t, s.direction), a = Cartesian3.magnitude(n);
  if (Cartesian3.normalize(n, n), !(!defined$2(i = raySphere(s, r, i)) || i.stop < 0 || i.start > a))
    return i.start = Math.max(i.start, 0), i.stop = Math.min(i.stop, a), i;
};
var scratchQ = new Vector3$1(), scratchW = new Vector3$1();
function addWithCancellationCheck(t, e, r) {
  var i = t + e;
  return CesiumMath.sign(t) !== CesiumMath.sign(e) && Math.abs(i / Math.max(Math.abs(t), Math.abs(e))) < r ? 0 : i;
}
function quadraticVectorExpression(t, e, r, i, s) {
  var n, a = i * i, o = s * s, h = (t[StaticMatrix3.COLUMN1ROW1] - t[StaticMatrix3.COLUMN2ROW2]) * o, l = s * (i * addWithCancellationCheck(t[StaticMatrix3.COLUMN1ROW0], t[StaticMatrix3.COLUMN0ROW1], CesiumMath.EPSILON15) + e.y), c = t[StaticMatrix3.COLUMN0ROW0] * a + t[StaticMatrix3.COLUMN2ROW2] * o + i * e.x + r, u = o * addWithCancellationCheck(t[StaticMatrix3.COLUMN2ROW1], t[StaticMatrix3.COLUMN1ROW2], CesiumMath.EPSILON15), d = s * (i * addWithCancellationCheck(t[StaticMatrix3.COLUMN2ROW0], t[StaticMatrix3.COLUMN0ROW2]) + e.z), p = [];
  if (0 === d && 0 === u) {
    if (0 === (n = QuadraticRealPolynomial.computeRealRoots(h, l, c)).length)
      return p;
    var f = n[0], _ = Math.sqrt(Math.max(1 - f * f, 0));
    if (p.push(new Vector3$1(i, s * f, s * -_)), p.push(new Vector3$1(i, s * f, s * _)), 2 === n.length) {
      var y = n[1], m = Math.sqrt(Math.max(1 - y * y, 0));
      p.push(new Vector3$1(i, s * y, s * -m)), p.push(new Vector3$1(i, s * y, s * m));
    }
    return p;
  }
  var g = d * d, x = u * u, M = d * u, w = h * h + x, b = 2 * (l * h + M), v = 2 * c * h + l * l - x + g, S = 2 * (c * l - M), C = c * c - g;
  if (0 === w && 0 === b && 0 === v && 0 === S)
    return p;
  var E = (n = QuarticRealPolynomial.computeRealRoots(w, b, v, S, C)).length;
  if (0 === E)
    return p;
  for (var P = 0; P < E; ++P) {
    var T = n[P], I = T * T, z = Math.max(1 - I, 0), A2 = Math.sqrt(z), O = (CesiumMath.sign(h) === CesiumMath.sign(c) ? addWithCancellationCheck(h * I + c, l * T, CesiumMath.EPSILON12) : CesiumMath.sign(c) === CesiumMath.sign(l * T) ? addWithCancellationCheck(h * I, l * T + c, CesiumMath.EPSILON12) : addWithCancellationCheck(h * I + l * T, c, CesiumMath.EPSILON12)) * addWithCancellationCheck(u * T, d, CesiumMath.EPSILON15);
    O < 0 ? p.push(new Vector3$1(i, s * T, s * A2)) : O > 0 ? p.push(new Vector3$1(i, s * T, s * -A2)) : 0 !== A2 ? (p.push(new Vector3$1(i, s * T, s * -A2)), p.push(new Vector3$1(i, s * T, s * A2)), ++P) : p.push(new Vector3$1(i, s * T, s * A2));
  }
  return p;
}
IntersectionTests.rayEllipsoid = function(t, e) {
  if (!defined$2(t))
    throw new DeveloperError("ray is required.");
  if (!defined$2(e))
    throw new DeveloperError("ellipsoid is required.");
  var r, i, s, n, a, o = e.oneOverRadii, h = Cartesian3.multiplyComponents(o, t.origin, scratchQ), l = Cartesian3.multiplyComponents(o, t.direction, scratchW), c = Cartesian3.magnitudeSquared(h), u = Cartesian3.dot(h, l);
  if (c > 1) {
    if (u >= 0)
      return;
    var d = u * u;
    if (r = c - 1, d < (s = (i = Cartesian3.magnitudeSquared(l)) * r))
      return;
    if (d > s) {
      n = u * u - s;
      var p = (a = -u + Math.sqrt(n)) / i, f = r / a;
      return p < f ? new Interval(p, f) : { start: f, stop: p };
    }
    var _ = Math.sqrt(r / i);
    return new Interval(_, _);
  }
  return c < 1 ? (r = c - 1, n = u * u - (s = (i = Cartesian3.magnitudeSquared(l)) * r), new Interval(0, (a = -u + Math.sqrt(n)) / i)) : u < 0 ? new Interval(0, -u / (i = Cartesian3.magnitudeSquared(l))) : void 0;
};
var firstAxisScratch = new Vector3$1(), secondAxisScratch = new Vector3$1(), thirdAxisScratch = new Vector3$1(), referenceScratch = new Vector3$1(), bCart = new Vector3$1(), bScratch = new Matrix3(), btScratch = new Matrix3(), diScratch = new Matrix3(), dScratch = new Matrix3(), cScratch = new Matrix3(), tempMatrix = new Matrix3(), aScratch = new Matrix3(), sScratch = new Vector3$1(), closestScratch = new Vector3$1(), surfPointScratch = new Vector3$1();
IntersectionTests.grazingAltitudeLocation = function(t, e) {
  if (!defined$2(t))
    throw new DeveloperError("ray is required.");
  if (!defined$2(e))
    throw new DeveloperError("ellipsoid is required.");
  var r = t.origin, i = t.direction;
  if (!Cartesian3.equals(r, Cartesian3.ZERO)) {
    var s = e.geodeticSurfaceNormal(r, firstAxisScratch);
    if (Cartesian3.dot(i, s) >= 0)
      return r;
  }
  var n = defined$2(this.rayEllipsoid(t, e)), a = e.transformPositionToScaledSpace(i, firstAxisScratch), o = Cartesian3.normalize(a, a), h = Cartesian3.mostOrthogonalAxis(a, referenceScratch), l = Cartesian3.normalize(Cartesian3.cross(h, o, secondAxisScratch), secondAxisScratch), c = Cartesian3.normalize(Cartesian3.cross(o, l, thirdAxisScratch), thirdAxisScratch), u = bScratch;
  u[0] = o.x, u[1] = o.y, u[2] = o.z, u[3] = l.x, u[4] = l.y, u[5] = l.z, u[6] = c.x, u[7] = c.y, u[8] = c.z;
  var d = StaticMatrix3.transpose(u, btScratch), p = StaticMatrix3.fromScale(e.radii, diScratch), f = StaticMatrix3.fromScale(e.oneOverRadii, dScratch), _ = cScratch;
  _[0] = 0, _[1] = -i.z, _[2] = i.y, _[3] = i.z, _[4] = 0, _[5] = -i.x, _[6] = -i.y, _[7] = i.x, _[8] = 0;
  var y, m, g = StaticMatrix3.multiply(StaticMatrix3.multiply(d, f, tempMatrix), _, tempMatrix), x = StaticMatrix3.multiply(StaticMatrix3.multiply(g, p, aScratch), u, aScratch), M = StaticMatrix3.multiplyByVector(g, r, bCart), w = quadraticVectorExpression(x, Cartesian3.negate(M, firstAxisScratch), 0, 0, 1), b = w.length;
  if (b > 0) {
    for (var v = Cartesian3.clone(Cartesian3.ZERO, closestScratch), S = Number.NEGATIVE_INFINITY, C = 0; C < b; ++C) {
      y = StaticMatrix3.multiplyByVector(p, StaticMatrix3.multiplyByVector(u, w[C], sScratch), sScratch);
      var E = Cartesian3.normalize(Cartesian3.subtract(y, r, referenceScratch), referenceScratch), P = Cartesian3.dot(E, i);
      P > S && (S = P, v = Cartesian3.clone(y, v));
    }
    var T = e.cartesianToCartographic(v, surfPointScratch);
    return S = CesiumMath.clamp(S, 0, 1), m = Cartesian3.magnitude(Cartesian3.subtract(v, r, referenceScratch)) * Math.sqrt(1 - S * S), m = n ? -m : m, T.z = m, e.cartographicToCartesian(T, new Vector3$1());
  }
};
var lineSegmentPlaneDifference = new Vector3$1();
IntersectionTests.lineSegmentPlane = function(t, e, r, i) {
  if (!defined$2(t))
    throw new DeveloperError("endPoint0 is required.");
  if (!defined$2(e))
    throw new DeveloperError("endPoint1 is required.");
  if (!defined$2(r))
    throw new DeveloperError("plane is required.");
  defined$2(i) || (i = new Vector3$1());
  var s = Cartesian3.subtract(e, t, lineSegmentPlaneDifference), n = r.normal, a = Cartesian3.dot(n, s);
  if (!(Math.abs(a) < CesiumMath.EPSILON6)) {
    var o = Cartesian3.dot(n, t), h = -(r.constant + o) / a;
    if (!(h < 0 || h > 1))
      return Cartesian3.multiplyByScalar(s, h, i), Cartesian3.add(t, i, i), i;
  }
}, IntersectionTests.trianglePlaneIntersection = function(t, e, r, i) {
  if (!(defined$2(t) && defined$2(e) && defined$2(r) && defined$2(i)))
    throw new DeveloperError("p0, p1, p2, and plane are required.");
  var s, n, a = i.normal, o = i.constant, h = Cartesian3.dot(a, t) + o < 0, l = Cartesian3.dot(a, e) + o < 0, c = Cartesian3.dot(a, r) + o < 0, u = 0;
  if (u += h ? 1 : 0, u += l ? 1 : 0, 1 !== (u += c ? 1 : 0) && 2 !== u || (s = new Vector3$1(), n = new Vector3$1()), 1 === u) {
    if (h)
      return IntersectionTests.lineSegmentPlane(t, e, i, s), IntersectionTests.lineSegmentPlane(t, r, i, n), { positions: [t, e, r, s, n], indices: [0, 3, 4, 1, 2, 4, 1, 4, 3] };
    if (l)
      return IntersectionTests.lineSegmentPlane(e, r, i, s), IntersectionTests.lineSegmentPlane(e, t, i, n), { positions: [t, e, r, s, n], indices: [1, 3, 4, 2, 0, 4, 2, 4, 3] };
    if (c)
      return IntersectionTests.lineSegmentPlane(r, t, i, s), IntersectionTests.lineSegmentPlane(r, e, i, n), { positions: [t, e, r, s, n], indices: [2, 3, 4, 0, 1, 4, 0, 4, 3] };
  } else if (2 === u) {
    if (!h)
      return IntersectionTests.lineSegmentPlane(e, t, i, s), IntersectionTests.lineSegmentPlane(r, t, i, n), { positions: [t, e, r, s, n], indices: [1, 2, 4, 1, 4, 3, 0, 3, 4] };
    if (!l)
      return IntersectionTests.lineSegmentPlane(r, e, i, s), IntersectionTests.lineSegmentPlane(t, e, i, n), { positions: [t, e, r, s, n], indices: [2, 0, 4, 2, 4, 3, 1, 3, 4] };
    if (!c)
      return IntersectionTests.lineSegmentPlane(t, r, i, s), IntersectionTests.lineSegmentPlane(e, r, i, n), { positions: [t, e, r, s, n], indices: [0, 1, 4, 0, 4, 3, 2, 3, 4] };
  }
};
class Cartesian4 {
  static clone(t, e) {
    return e || (e = new Vector4()), e.copy(t), e;
  }
  static fromElements(t, e, r, i, s) {
    return s || (s = new Vector4()), s.set(t, e, r, i), s;
  }
  static lerp(t, e, r, i) {
    return i || (i = new Vector4()), i.lerpVectors(t, e, r), i;
  }
  static equals(t, e) {
    return t.equals(e);
  }
  static normalize(t, e) {
    return t === e ? (t.normalize(), t) : (e.copy(t), e.normalize(), e);
  }
  static add(t, e, r) {
    return r || (r = new Vector4()), r.addVectors(t, e);
  }
  static multiplyByScalar(t, e, r) {
    return r || (r = new Vector4()), r.copy(t).multiplyScalar(e), r;
  }
  static subtract(t, e, r) {
    return r || (r = new Vector4()), r.subVectors(t, e), r;
  }
  static distance(t, e) {
    return t.distanceTo(e);
  }
}
__publicField(Cartesian4, "ZERO", new Vector4(0, 0, 0, 0));
__publicField(Cartesian4, "UNIT_W", Object.freeze(new Vector4(0, 0, 0, 1)));
const scratchNormal = new Vector3$1(), scratchCartesian$1 = new Vector3$1(), scratchInverseTranspose = new Matrix4(), scratchPlaneCartesian4 = new Vector4(0, 0, 0, 0), scratchTransformNormal = new Vector3$1();
class StaticPlane {
  static fromPointNormal(t, e, r) {
    return r || (r = new Plane()), r.setFromNormalAndCoplanarPoint(e, t), r;
  }
  static fromCartesian4(t, e) {
    const r = Cartesian3.fromCartesian4(t, scratchNormal), i = t.w;
    if (!CesiumMath.equalsEpsilon(Cartesian3.magnitude(r), 1, CesiumMath.EPSILON6))
      throw new Error("normal must be normalized.");
    return defined$2(e) ? (Cartesian3.clone(r, e.normal), e.constant = i, e) : new StaticPlane(r, i);
  }
  static getPointDistance(t, e) {
    return Cartesian3.dot(t.normal, e) + t.constant;
  }
  static projectPointOntoPlane(t, e, r) {
    defined$2(r) || (r = new Vector3$1());
    const i = StaticPlane.getPointDistance(t, e), s = Cartesian3.multiplyByScalar(t.normal, i, scratchCartesian$1);
    return Cartesian3.subtract(e, s, r);
  }
  static transform(t, e, r) {
    const i = t.normal, s = t.constant, n = Matrix4.inverseTranspose(e, scratchInverseTranspose);
    let a = Cartesian4.fromElements(i.x, i.y, i.z, s, scratchPlaneCartesian4);
    a = Matrix4.multiplyByVector(n, a, a);
    const o = Cartesian3.fromCartesian4(a, scratchTransformNormal);
    return a = Cartesian4.divideByScalar(a, Cartesian3.magnitude(o), a), Plane.fromCartesian4(a, r);
  }
  static clone(t, e) {
    return defined$2(e) ? (Cartesian3.clone(t.normal, e.normal), e.constant = t.constant, e) : new StaticPlane(t.normal, t.constant);
  }
  static equals(t, e) {
    return t.constant === e.constant && Cartesian3.equals(t.normal, e.normal);
  }
}
Plane.ORIGIN_XY_PLANE = Object.freeze(new StaticPlane(Cartesian3.UNIT_Z, 0)), Plane.ORIGIN_YZ_PLANE = Object.freeze(new StaticPlane(Cartesian3.UNIT_X, 0)), Plane.ORIGIN_ZX_PLANE = Object.freeze(new StaticPlane(Cartesian3.UNIT_Y, 0));
const scratchCart4 = new Vector4(0, 0, 0, 0), scratchProjectPointOntoPlaneRay$1 = new Ray(), scratchProjectPointOntoPlaneCartesian3$1 = new Vector3$1();
class EllipsoidTangentPlane {
  constructor(t, e) {
    if (!defined$2(t = (e = defaultValue(e, Ellipsoid.WGS84)).scaleToGeodeticSurface(t)))
      throw new DeveloperError("origin must not be at the center of the ellipsoid.");
    const r = Transforms.eastNorthUpToFixedFrame(t, e);
    this._ellipsoid = e, this._origin = t, this._xAxis = Cartesian3.fromCartesian4(StaticMatrix4.getColumn(r, 0, scratchCart4)), this._yAxis = Cartesian3.fromCartesian4(StaticMatrix4.getColumn(r, 1, scratchCart4));
    const i = Cartesian3.fromCartesian4(StaticMatrix4.getColumn(r, 2, scratchCart4));
    this._plane = StaticPlane.fromPointNormal(t, i);
  }
  static fromPoints(t, e) {
    let r = t[0].x, i = t[0].y, s = t[0].z, n = t[0].x, a = t[0].y, o = t[0].z;
    for (let e2 = 0; e2 < t.length; e2++) {
      const h2 = t[e2], l2 = h2.x, c2 = h2.y, u2 = h2.z;
      r = Math.min(l2, r), n = Math.max(l2, n), i = Math.min(c2, i), a = Math.max(c2, a), s = Math.min(u2, s), o = Math.max(u2, o);
    }
    const h = new Vector3$1(r, i, s), l = new Vector3$1(n, a, o), c = new Box3(h, l);
    let u = new Vector3$1();
    return u = c.getCenter(u), new EllipsoidTangentPlane(u, e);
  }
  projectPointToNearestOnPlane(t, e) {
    defined$2(e) || (e = new Cartesian2());
    const r = scratchProjectPointOntoPlaneRay$1;
    r.origin = t, Cartesian3.clone(this._plane.normal, r.direction);
    let i = IntersectionTests.rayPlane(r, this._plane, scratchProjectPointOntoPlaneCartesian3$1);
    if (defined$2(i) || (Cartesian3.negate(r.direction, r.direction), i = IntersectionTests.rayPlane(r, this._plane, scratchProjectPointOntoPlaneCartesian3$1)), defined$2(i)) {
      const t4 = Cartesian3.subtract(i, this._origin, i), r2 = Cartesian3.dot(this._xAxis, t4), s = Cartesian3.dot(this._yAxis, t4);
      return defined$2(e) ? (e.x = r2, e.y = s, e) : new Vector2(r2, s);
    }
  }
  projectPointsOntoPlane(t, e) {
    defined$2(e) || (e = []);
    let r = 0;
    const i = t.length;
    for (let s = 0; s < i; s++) {
      const i2 = this.projectPointOntoPlane(t[s], e[r]);
      i2 && (e[r] = i2, r++);
    }
    return e.length = r, e;
  }
  projectPointOntoPlane(t, e) {
    const r = scratchProjectPointOntoPlaneRay$1;
    r.origin = t, Cartesian3.normalize(t, r.direction);
    let i = IntersectionTests.rayPlane(r, this._plane, scratchProjectPointOntoPlaneCartesian3$1);
    if (defined$2(i) || (Cartesian3.negate(r.direction, r.direction), i = IntersectionTests.rayPlane(r, this._plane, scratchProjectPointOntoPlaneCartesian3$1)), defined$2(i)) {
      const t4 = Cartesian3.subtract(i, this._origin, i), r2 = Cartesian3.dot(this._xAxis, t4), s = Cartesian3.dot(this._yAxis, t4);
      return defined$2(e) ? (e.x = r2, e.y = s, e) : new Vector2(r2, s);
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
    const r = t.z < 0 ? -1 : 1;
    let i = _Stereographic.NORTH_POLE_TANGENT_PLANE, s = _Stereographic.SOUTH_POLE;
    r < 0 && (i = _Stereographic.SOUTH_POLE_TANGENT_PLANE, s = _Stereographic.NORTH_POLE);
    const n = scratchProjectPointOntoPlaneRay;
    n.origin = i.ellipsoid.scaleToGeocentricSurface(t, n.origin), n.direction = Cartesian3.subtract(n.origin, s, scratchProjectPointOntoPlaneRayDirection), Cartesian3.normalize(n.direction, n.direction);
    const a = IntersectionTests.rayPlane(n, i.plane, scratchProjectPointOntoPlaneCartesian3), o = Cartesian3.subtract(a, s, a), h = Cartesian3.dot(i.xAxis, o), l = r * Cartesian3.dot(i.yAxis, o);
    return e ? (e.position = new Vector2(h, l), e.tangentPlane = i, e) : new _Stereographic(new Vector2(h, l), i);
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
function sortPolygonPoints(t) {
  if (t.length < 3)
    throw new Error("\u70B9\u7684\u6570\u91CF\u5FC5\u987B\u5927\u4E8E\u7B49\u4E8E3\u4E2A\u624D\u80FD\u5F62\u6210\u591A\u8FB9\u5F62");
  const e = calculateCentroid(t), r = t.map((t4, r2) => ({ index: r2, angle: Math.atan2(t4.y - e.y, t4.x - e.x) }));
  return r.sort((t4, e2) => t4.angle - e2.angle), r.map((t4) => t4.index);
}
function calculateCentroid(t, e) {
  e || (e = new Vector2());
  const r = t.length;
  let i = 0, s = 0;
  return t.forEach((t4) => {
    i += t4.x, s += t4.y;
  }), e.set(i / r, s / r);
}
function defined(t) {
  return null != t;
}
function getLineIntersection(t, e, r, i, s, n) {
  const a = t.x, o = t.y, h = e.x, l = e.y, c = r.x, u = r.y, d = i.x, p = i.y, f = (a - h) * (u - p) - (o - l) * (c - d);
  if (Math.abs(f) < 1e-4)
    return null;
  const _ = ((a - c) * (u - p) - (o - u) * (c - d)) / f, y = -((a - h) * (o - u) - (o - l) * (a - c)) / f;
  return _ >= 0 && _ <= 1 && y >= 0 && y <= 1 ? { point: t.clone().lerp(e, _), uv: s.clone().lerp(n, _) } : null;
}
function isPointInTriangle(t, e, r, i) {
  const s = r.x - e.x, n = r.y - e.y, a = i.x - e.x, o = i.y - e.y, h = t.x - e.x, l = t.y - e.y, c = s * o - n * a, u = (h * o - l * a) / c, d = (s * l - n * h) / c;
  return u >= 0 && d >= 0 && u + d <= 1;
}
function isPointInRectangle(t, e, r, i, s) {
  return t.x >= e && t.x <= r && t.y >= i && t.y <= s;
}
function getRectanglePoints(t, e, r, i) {
  return [new Vector3$1(t, r, 0), new Vector3$1(e, r, 0), new Vector3$1(e, i, 0), new Vector3$1(t, i, 0)];
}
function getRectangleEdges(t) {
  return [[t[0], t[1]], [t[1], t[2]], [t[2], t[3]], [t[3], t[0]]];
}
function calculateIntersection(t, e, r) {
  const i = [];
  t.forEach((t4, s2) => {
    if (isPointInRectangle(t4, r[0].x, r[2].x, r[0].y, r[2].y)) {
      const r2 = t4.x + "," + t4.y + "," + t4.z;
      i[r2] || (i[r2] = { point: t4, uv: e[s2] });
    }
  });
  const s = Math.min(t[0].x, Math.min(t[1].x, t[2].x)), n = Math.max(t[0].x, Math.max(t[1].x, t[2].x)), a = Math.min(t[0].y, Math.min(t[1].y, t[2].y)), o = Math.max(t[0].y, Math.max(t[1].y, t[2].y)), h = Math.min(e[0].x, Math.min(e[1].x, e[2].x)), l = Math.max(e[0].x, Math.max(e[1].x, e[2].x)), c = Math.min(e[0].y, Math.min(e[1].y, e[2].y)), u = Math.max(e[0].y, Math.max(e[1].y, e[2].y)), d = n - s, p = o - a, f = l - h, _ = u - c;
  r.forEach((e2) => {
    if (isPointInTriangle(e2, ...t)) {
      const t4 = e2.x + "," + e2.y + "," + e2.z;
      if (!i[t4]) {
        const r2 = h + f * (e2.x - s) / d, n2 = c + _ * (e2.y - a) / p;
        i[t4] = { point: e2, uv: new Vector2(r2, n2) };
      }
    }
  });
  const y = getRectangleEdges(r);
  return [[0, 1], [1, 2], [2, 0]].forEach(([r2, s2]) => {
    y.forEach(([n2, a2]) => {
      const o2 = getLineIntersection(t[r2], t[s2], n2, a2, e[r2], e[s2]);
      if (o2) {
        const { point: t4 } = o2, e2 = t4.x + "," + t4.y + "," + t4.z;
        i[e2] || (i[e2] = o2);
      }
    });
  }), Object.values(i);
}
new Stereographic(), new Stereographic(), new Vector2(), new Vector2(), new Stereographic(), new Vector2(), Object.freeze({}), Object.freeze(new Matrix4());
const subdivisionV0Scratch = new Vector3$1(), subdivisionV1Scratch = new Vector3$1(), subdivisionV2Scratch = new Vector3$1();
new Vector3$1(), new Vector3$1(), new Vector3$1();
const subdivisionMidScratch = new Vector3$1(), subdivisionT0Scratch = new Vector2(), subdivisionT1Scratch = new Vector2(), subdivisionT2Scratch = new Vector2(), subdivideWithRange = (t, e, r, i, s, n, a, o, h) => {
  const l = [];
  for (let e2 = 0; e2 < t.length; e2++) {
    const i2 = t[e2];
    h[`${i2.x},${i2.y},${i2.z}`] = r[e2];
    isPointInRectangle(i2, o[0], o[1], o[0], o[1]) && l.push(e2);
  }
  if (3 === l.length)
    return false;
  const c = getRectanglePoints(o[0], o[1], o[0], o[1]), u = calculateIntersection([t[0], t[1], t[2]], e, c), d = u.map((t4) => t4.point), p = u.map((t4) => t4.uv);
  if (d.length > 2) {
    const t4 = sortPolygonPoints(d), e2 = [];
    for (let r2 = 0, a2 = t4.length; r2 < a2; r2++) {
      const a3 = t4[r2], o2 = d[a3], l2 = p[a3], c2 = `${o2.x},${o2.y},${o2.z}`;
      defined(h[c2]) || (s.push(o2.x, o2.y, o2.z), i && n.push(l2.x, l2.y), h[c2] = s.length / 3 - 1);
      const u2 = h[c2];
      e2.push(u2);
    }
    3 === d.length ? a.push(e2[1], e2[2], e2[0]) : 4 === d.length ? a.push(e2[2], e2[3], e2[0], e2[0], e2[1], e2[2]) : 5 === d.length && a.push(e2[3], e2[4], e2[0], e2[0], e2[1], e2[2], e2[2], e2[3], e2[0]);
  }
  return true;
}, subdivideMesh = (t, e, r, i, s = 0, n = [-0.501, 0.501]) => {
  const a = !!r, o = Array.from(e);
  let h = 0;
  const l = t.length;
  let c = null, u = null;
  if (Array.isArray(t[0])) {
    c = [];
    let e2 = 0;
    for (h = 0; h < l; h++) {
      const r2 = t[h];
      c[e2++] = r2[0], c[e2++] = r2[1], c[e2++] = r2[2];
    }
  } else
    c = Array.from(t);
  if (a) {
    if (Array.isArray(r[0])) {
      u = [];
      let t4 = 0;
      for (h = 0; h < r.length; h++) {
        const e2 = r[h];
        u[t4++] = e2[0], u[t4++] = e2[1];
      }
    } else
      u = Array.from(r);
  }
  const d = [], p = {}, f = {}, _ = i * i;
  for (; o.length > 0; ) {
    const t4 = o.pop(), e2 = o.pop(), r2 = o.pop(), i2 = subdivisionV0Scratch.fromArray(c, 3 * r2), l2 = subdivisionV1Scratch.fromArray(c, 3 * e2), y = subdivisionV2Scratch.fromArray(c, 3 * t4);
    let m, g, x;
    a && (m = subdivisionT0Scratch.fromArray(u, 2 * r2), g = subdivisionT1Scratch.fromArray(u, 2 * e2), x = subdivisionT2Scratch.fromArray(u, 2 * t4));
    const M = Cartesian3.magnitudeSquared(Cartesian3.subtract(i2, l2, subdivisionMidScratch)), w = Cartesian3.magnitudeSquared(Cartesian3.subtract(l2, y, subdivisionMidScratch)), b = Cartesian3.magnitudeSquared(Cartesian3.subtract(y, i2, subdivisionMidScratch)), v = Math.max(M, w, b);
    let S, C, E;
    if (v > _)
      M === v ? (S = `${Math.min(r2, e2)} ${Math.max(r2, e2)}`, h = p[S], null == h && (C = Cartesian3.add(i2, l2, subdivisionMidScratch), Cartesian3.multiplyByScalar(C, 0.5, C), c.push(C.x, C.y, C.z), h = c.length / 3 - 1, p[S] = h, a && (E = Cartesian2.add(m, g, subdivisionMidScratch), Cartesian2.multiplyByScalar(E, 0.5, E), u.push(E.x, E.y))), o.push(r2, h, t4), o.push(h, e2, t4)) : w === v ? (S = `${Math.min(e2, t4)} ${Math.max(e2, t4)}`, h = p[S], h || (C = Cartesian3.add(l2, y, subdivisionMidScratch), Cartesian3.multiplyByScalar(C, 0.5, C), c.push(C.x, C.y, C.z), h = c.length / 3 - 1, p[S] = h, a && (E = Cartesian2.add(g, x, subdivisionMidScratch), Cartesian2.multiplyByScalar(E, 0.5, E), u.push(E.x, E.y))), o.push(e2, h, r2), o.push(h, t4, r2)) : b === v && (S = `${Math.min(t4, r2)} ${Math.max(t4, r2)}`, h = p[S], h || (C = Cartesian3.add(y, i2, subdivisionMidScratch), Cartesian3.multiplyByScalar(C, 0.5, C), c.push(C.x, C.y, C.z), h = c.length / 3 - 1, p[S] = h, a && (E = Cartesian2.add(x, m, subdivisionMidScratch), Cartesian2.multiplyByScalar(E, 0.5, E), u.push(E.x, E.y))), o.push(t4, h, e2), o.push(h, r2, e2));
    else {
      if (subdivideWithRange([i2, l2, y], [m, g, x], [r2, e2, t4], a, c, u, o, n, f))
        continue;
      d.push(r2 + s), d.push(e2 + s), d.push(t4 + s);
    }
  }
  return { vertices: c, uvs: u, indices: d };
};
function clamp(t, e, r) {
  return Math.max(e, Math.min(r, t));
}
class Quaternion {
  constructor(t = 0, e = 0, r = 0, i = 1) {
    this.isQuaternion = true, this._x = t, this._y = e, this._z = r, this._w = i;
  }
  static slerpFlat(t, e, r, i, s, n, a) {
    let o = r[i + 0], h = r[i + 1], l = r[i + 2], c = r[i + 3];
    const u = s[n + 0], d = s[n + 1], p = s[n + 2], f = s[n + 3];
    if (0 === a)
      return t[e + 0] = o, t[e + 1] = h, t[e + 2] = l, void (t[e + 3] = c);
    if (1 === a)
      return t[e + 0] = u, t[e + 1] = d, t[e + 2] = p, void (t[e + 3] = f);
    if (c !== f || o !== u || h !== d || l !== p) {
      let t4 = 1 - a;
      const e2 = o * u + h * d + l * p + c * f, r2 = e2 >= 0 ? 1 : -1, i2 = 1 - e2 * e2;
      if (i2 > Number.EPSILON) {
        const s3 = Math.sqrt(i2), n2 = Math.atan2(s3, e2 * r2);
        t4 = Math.sin(t4 * n2) / s3, a = Math.sin(a * n2) / s3;
      }
      const s2 = a * r2;
      if (o = o * t4 + u * s2, h = h * t4 + d * s2, l = l * t4 + p * s2, c = c * t4 + f * s2, t4 === 1 - a) {
        const t5 = 1 / Math.sqrt(o * o + h * h + l * l + c * c);
        o *= t5, h *= t5, l *= t5, c *= t5;
      }
    }
    t[e] = o, t[e + 1] = h, t[e + 2] = l, t[e + 3] = c;
  }
  static multiplyQuaternionsFlat(t, e, r, i, s, n) {
    const a = r[i], o = r[i + 1], h = r[i + 2], l = r[i + 3], c = s[n], u = s[n + 1], d = s[n + 2], p = s[n + 3];
    return t[e] = a * p + l * c + o * d - h * u, t[e + 1] = o * p + l * u + h * c - a * d, t[e + 2] = h * p + l * d + a * u - o * c, t[e + 3] = l * p - a * c - o * u - h * d, t;
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
  set(t, e, r, i) {
    return this._x = t, this._y = e, this._z = r, this._w = i, this._onChangeCallback(), this;
  }
  clone() {
    return new this.constructor(this._x, this._y, this._z, this._w);
  }
  copy(t) {
    return this._x = t.x, this._y = t.y, this._z = t.z, this._w = t.w, this._onChangeCallback(), this;
  }
  setFromEuler(t, e = true) {
    const r = t._x, i = t._y, s = t._z, n = t._order, a = Math.cos, o = Math.sin, h = a(r / 2), l = a(i / 2), c = a(s / 2), u = o(r / 2), d = o(i / 2), p = o(s / 2);
    switch (n) {
      case "XYZ":
        this._x = u * l * c + h * d * p, this._y = h * d * c - u * l * p, this._z = h * l * p + u * d * c, this._w = h * l * c - u * d * p;
        break;
      case "YXZ":
        this._x = u * l * c + h * d * p, this._y = h * d * c - u * l * p, this._z = h * l * p - u * d * c, this._w = h * l * c + u * d * p;
        break;
      case "ZXY":
        this._x = u * l * c - h * d * p, this._y = h * d * c + u * l * p, this._z = h * l * p + u * d * c, this._w = h * l * c - u * d * p;
        break;
      case "ZYX":
        this._x = u * l * c - h * d * p, this._y = h * d * c + u * l * p, this._z = h * l * p - u * d * c, this._w = h * l * c + u * d * p;
        break;
      case "YZX":
        this._x = u * l * c + h * d * p, this._y = h * d * c + u * l * p, this._z = h * l * p - u * d * c, this._w = h * l * c - u * d * p;
        break;
      case "XZY":
        this._x = u * l * c - h * d * p, this._y = h * d * c - u * l * p, this._z = h * l * p + u * d * c, this._w = h * l * c + u * d * p;
        break;
      default:
        console.warn("THREE.Quaternion: .setFromEuler() encountered an unknown order: " + n);
    }
    return true === e && this._onChangeCallback(), this;
  }
  setFromAxisAngle(t, e) {
    const r = e / 2, i = Math.sin(r);
    return this._x = t.x * i, this._y = t.y * i, this._z = t.z * i, this._w = Math.cos(r), this._onChangeCallback(), this;
  }
  setFromRotationMatrix(t) {
    const e = t.elements, r = e[0], i = e[4], s = e[8], n = e[1], a = e[5], o = e[9], h = e[2], l = e[6], c = e[10], u = r + a + c;
    if (u > 0) {
      const t4 = 0.5 / Math.sqrt(u + 1);
      this._w = 0.25 / t4, this._x = (l - o) * t4, this._y = (s - h) * t4, this._z = (n - i) * t4;
    } else if (r > a && r > c) {
      const t4 = 2 * Math.sqrt(1 + r - a - c);
      this._w = (l - o) / t4, this._x = 0.25 * t4, this._y = (i + n) / t4, this._z = (s + h) / t4;
    } else if (a > c) {
      const t4 = 2 * Math.sqrt(1 + a - r - c);
      this._w = (s - h) / t4, this._x = (i + n) / t4, this._y = 0.25 * t4, this._z = (o + l) / t4;
    } else {
      const t4 = 2 * Math.sqrt(1 + c - r - a);
      this._w = (n - i) / t4, this._x = (s + h) / t4, this._y = (o + l) / t4, this._z = 0.25 * t4;
    }
    return this._onChangeCallback(), this;
  }
  setFromUnitVectors(t, e) {
    let r = t.dot(e) + 1;
    return r < 1e-8 ? (r = 0, Math.abs(t.x) > Math.abs(t.z) ? (this._x = -t.y, this._y = t.x, this._z = 0, this._w = r) : (this._x = 0, this._y = -t.z, this._z = t.y, this._w = r)) : (this._x = t.y * e.z - t.z * e.y, this._y = t.z * e.x - t.x * e.z, this._z = t.x * e.y - t.y * e.x, this._w = r), this.normalize();
  }
  angleTo(t) {
    return 2 * Math.acos(Math.abs(clamp(this.dot(t), -1, 1)));
  }
  rotateTowards(t, e) {
    const r = this.angleTo(t);
    if (0 === r)
      return this;
    const i = Math.min(1, e / r);
    return this.slerp(t, i), this;
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
    const r = t._x, i = t._y, s = t._z, n = t._w, a = e._x, o = e._y, h = e._z, l = e._w;
    return this._x = r * l + n * a + i * h - s * o, this._y = i * l + n * o + s * a - r * h, this._z = s * l + n * h + r * o - i * a, this._w = n * l - r * a - i * o - s * h, this._onChangeCallback(), this;
  }
  slerp(t, e) {
    if (0 === e)
      return this;
    if (1 === e)
      return this.copy(t);
    const r = this._x, i = this._y, s = this._z, n = this._w;
    let a = n * t._w + r * t._x + i * t._y + s * t._z;
    if (a < 0 ? (this._w = -t._w, this._x = -t._x, this._y = -t._y, this._z = -t._z, a = -a) : this.copy(t), a >= 1)
      return this._w = n, this._x = r, this._y = i, this._z = s, this;
    const o = 1 - a * a;
    if (o <= Number.EPSILON) {
      const t4 = 1 - e;
      return this._w = t4 * n + e * this._w, this._x = t4 * r + e * this._x, this._y = t4 * i + e * this._y, this._z = t4 * s + e * this._z, this.normalize(), this;
    }
    const h = Math.sqrt(o), l = Math.atan2(h, a), c = Math.sin((1 - e) * l) / h, u = Math.sin(e * l) / h;
    return this._w = n * c + this._w * u, this._x = r * c + this._x * u, this._y = i * c + this._y * u, this._z = s * c + this._z * u, this._onChangeCallback(), this;
  }
  slerpQuaternions(t, e, r) {
    return this.copy(t).slerp(e, r);
  }
  random() {
    const t = 2 * Math.PI * Math.random(), e = 2 * Math.PI * Math.random(), r = Math.random(), i = Math.sqrt(1 - r), s = Math.sqrt(r);
    return this.set(i * Math.sin(t), i * Math.cos(t), s * Math.sin(e), s * Math.cos(e));
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
  constructor(t = 0, e = 0, r = 0) {
    Vector3.prototype.isVector3 = true, this.x = t, this.y = e, this.z = r;
  }
  set(t, e, r) {
    return void 0 === r && (r = this.z), this.x = t, this.y = e, this.z = r, this;
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
    const e = this.x, r = this.y, i = this.z, s = t.elements;
    return this.x = s[0] * e + s[3] * r + s[6] * i, this.y = s[1] * e + s[4] * r + s[7] * i, this.z = s[2] * e + s[5] * r + s[8] * i, this;
  }
  applyNormalMatrix(t) {
    return this.applyMatrix3(t).normalize();
  }
  applyMatrix4(t) {
    const e = this.x, r = this.y, i = this.z, s = t.elements, n = 1 / (s[3] * e + s[7] * r + s[11] * i + s[15]);
    return this.x = (s[0] * e + s[4] * r + s[8] * i + s[12]) * n, this.y = (s[1] * e + s[5] * r + s[9] * i + s[13]) * n, this.z = (s[2] * e + s[6] * r + s[10] * i + s[14]) * n, this;
  }
  applyQuaternion(t) {
    const e = this.x, r = this.y, i = this.z, s = t.x, n = t.y, a = t.z, o = t.w, h = 2 * (n * i - a * r), l = 2 * (a * e - s * i), c = 2 * (s * r - n * e);
    return this.x = e + o * h + n * c - a * l, this.y = r + o * l + a * h - s * c, this.z = i + o * c + s * l - n * h, this;
  }
  project(t) {
    return this.applyMatrix4(t.matrixWorldInverse).applyMatrix4(t.projectionMatrix);
  }
  unproject(t) {
    return this.applyMatrix4(t.projectionMatrixInverse).applyMatrix4(t.matrixWorld);
  }
  transformDirection(t) {
    const e = this.x, r = this.y, i = this.z, s = t.elements;
    return this.x = s[0] * e + s[4] * r + s[8] * i, this.y = s[1] * e + s[5] * r + s[9] * i, this.z = s[2] * e + s[6] * r + s[10] * i, this.normalize();
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
    const r = this.length();
    return this.divideScalar(r || 1).multiplyScalar(clamp(r, t, e));
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
  lerpVectors(t, e, r) {
    return this.x = t.x + (e.x - t.x) * r, this.y = t.y + (e.y - t.y) * r, this.z = t.z + (e.z - t.z) * r, this;
  }
  cross(t) {
    return this.crossVectors(this, t);
  }
  crossVectors(t, e) {
    const r = t.x, i = t.y, s = t.z, n = e.x, a = e.y, o = e.z;
    return this.x = i * o - s * a, this.y = s * n - r * o, this.z = r * a - i * n, this;
  }
  projectOnVector(t) {
    const e = t.lengthSq();
    if (0 === e)
      return this.set(0, 0, 0);
    const r = t.dot(this) / e;
    return this.copy(t).multiplyScalar(r);
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
    const r = this.dot(t) / e;
    return Math.acos(clamp(r, -1, 1));
  }
  distanceTo(t) {
    return Math.sqrt(this.distanceToSquared(t));
  }
  distanceToSquared(t) {
    const e = this.x - t.x, r = this.y - t.y, i = this.z - t.z;
    return e * e + r * r + i * i;
  }
  manhattanDistanceTo(t) {
    return Math.abs(this.x - t.x) + Math.abs(this.y - t.y) + Math.abs(this.z - t.z);
  }
  setFromSpherical(t) {
    return this.setFromSphericalCoords(t.radius, t.phi, t.theta);
  }
  setFromSphericalCoords(t, e, r) {
    const i = Math.sin(e) * t;
    return this.x = i * Math.sin(r), this.y = Math.cos(e) * t, this.z = i * Math.cos(r), this;
  }
  setFromCylindrical(t) {
    return this.setFromCylindricalCoords(t.radius, t.theta, t.y);
  }
  setFromCylindricalCoords(t, e, r) {
    return this.x = t * Math.sin(e), this.y = r, this.z = t * Math.cos(e), this;
  }
  setFromMatrixPosition(t) {
    const e = t.elements;
    return this.x = e[12], this.y = e[13], this.z = e[14], this;
  }
  setFromMatrixScale(t) {
    const e = this.setFromMatrixColumn(t, 0).length(), r = this.setFromMatrixColumn(t, 1).length(), i = this.setFromMatrixColumn(t, 2).length();
    return this.x = e, this.y = r, this.z = i, this;
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
    const t = Math.random() * Math.PI * 2, e = 2 * Math.random() - 1, r = Math.sqrt(1 - e * e);
    return this.x = r * Math.cos(t), this.y = e, this.z = r * Math.sin(t), this;
  }
  *[Symbol.iterator]() {
    yield this.x, yield this.y, yield this.z;
  }
}
const _vector = new Vector3(), _quaternion = new Quaternion();
function isPointEqual(t, e, r = 1e-6) {
  return getDistance(t, e) < r;
}
function getDistance(t, e) {
  return Math.sqrt(Math.pow(t[0] - e[0], 2) + Math.pow(t[1] - e[1], 2) + Math.pow((t[2] || 0) - (e[2] || 0), 2));
}
function intepolateArray2(t, e, r) {
  return [t[0] + (e[0] - t[0]) * r, t[1] + (e[1] - t[1]) * r];
}
new Vector3(), new Vector3(), new Vector3(), new Vector3(), new Vector3(), new Vector3(), new Vector3(), new Vector3(), new Vector3(), new Vector3();
const _CoordTransformer = class {
  static _cacheKey(t, e) {
    return `${t}->${e}`;
  }
  static _clearPathCache() {
    _CoordTransformer._pathCache.clear();
  }
  static _findTransformPath(t, e, r = /* @__PURE__ */ new Set()) {
    const i = _CoordTransformer._cacheKey(t, e);
    if (_CoordTransformer._pathCache.has(i))
      return _CoordTransformer._pathCache.get(i);
    if (r.has(t))
      return _CoordTransformer._pathCache.set(i, null), null;
    if (r.add(t), _CoordTransformer._registeredTransformers[t] && _CoordTransformer._registeredTransformers[t][e]) {
      const r2 = [t, e];
      return _CoordTransformer._pathCache.set(i, r2), r2;
    }
    if (_CoordTransformer._registeredTransformers[t])
      for (const s of Object.keys(_CoordTransformer._registeredTransformers[t])) {
        const n = _CoordTransformer._findTransformPath(s, e, new Set(r));
        if (n) {
          const e2 = [t, ...n];
          return _CoordTransformer._pathCache.set(i, e2), e2;
        }
      }
    return _CoordTransformer._pathCache.set(i, null), null;
  }
  static transform(t, e, r, i) {
    if (t === e)
      return i.copy(r), i;
    const s = _CoordTransformer._findTransformPath(t, e);
    if (!s)
      return i.copy(r), i;
    let n = r;
    for (let t4 = 0; t4 < s.length - 1; t4++) {
      const e2 = s[t4], r2 = s[t4 + 1];
      n = _CoordTransformer._registeredTransformers[e2][r2](n, i);
    }
    return n;
  }
  static register(t, e, r) {
    _CoordTransformer._registeredTransformers[t] || (_CoordTransformer._registeredTransformers[t] = {}), _CoordTransformer._registeredTransformers[t][e] = r, _CoordTransformer._clearPathCache();
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
      for (const r of Object.keys(_CoordTransformer._registeredTransformers[e]))
        t[e][r] = _CoordTransformer._registeredTransformers[e][r].toString();
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
const _pointIn$1 = new Vector3$1(), _pointOut$1 = new Vector3$1(), reprojectCoordinate = (t, e, r, i, s, n) => {
  let a = CoordTransformer.canTransform(t, e);
  return r !== i && r && i || a ? (r.unprojectCoordinate(s, _pointOut$1), a ? CoordTransformer.transform(t, e, _pointOut$1, _pointIn$1) : _pointIn$1.copy(_pointOut$1), i.projectCoordinate(_pointIn$1, n), n) : (n.copy(s), n);
}, projectVertices = (t, e, r = true, i = true, s = false) => {
  if (!e.forceProjectCoordinates && !s && e.targetProjectionName === e.sourceProjectionName)
    return;
  const n = e.sourceProjection, a = e.targetProjection, o = e.sourceCoordType, h = e.targetCoordType, [l, c, u] = e.targetCenter, d = e.forceUseGeoBoundingBox || n.isGeo, p = d ? e.geoBoundingBox : e.projectedBoundingBox;
  let f, _, y, m;
  if (p.isBox3) {
    const t4 = p.min, e2 = p.max;
    f = t4.x, _ = t4.y, y = e2.x, m = e2.y;
  } else
    [f, _, , y, m] = p;
  const g = y - f, x = m - _;
  if (r)
    for (let e2 = 0, r2 = t.length - 2; e2 < r2; e2 += 3) {
      const r3 = t[e2], s2 = t[e2 + 1], p2 = t[e2 + 2];
      _pointIn$1.set(f + (r3 + 0.5) * g, _ + (s2 + 0.5) * x, p2), d ? (o !== h && CoordTransformer.transform(o, h, _pointIn$1, _pointIn$1), a.projectCoordinate(_pointIn$1, _pointOut$1)) : reprojectCoordinate(o, h, n, a, _pointIn$1, _pointOut$1), t[e2] = _pointOut$1.x, t[e2 + 1] = _pointOut$1.y, t[e2 + 2] = _pointOut$1.z, i && (t[e2] -= l, t[e2 + 1] -= c, t[e2 + 2] -= u);
    }
  else
    for (let e2 = 0, r2 = t.length; e2 < r2; e2 += 1) {
      const r3 = t[e2], s2 = r3[0], p2 = r3[1], y2 = r3[2];
      _pointIn$1.set(f + (s2 + 0.5) * g, _ + (p2 + 0.5) * x, y2), d ? (o !== h && CoordTransformer.transform(o, h, _pointIn$1, _pointIn$1), a.projectCoordinate(_pointIn$1, _pointOut$1)) : reprojectCoordinate(o, h, n, a, _pointIn$1, _pointOut$1), r3[0] = _pointOut$1.x, r3[1] = _pointOut$1.y, r3[2] = _pointOut$1.z, i && (r3[0] -= l, r3[1] -= c, r3[2] -= u);
    }
}, subdivideVertices = (t, e, r, i) => {
  if (i.sourceProjectionName === i.targetProjectionName)
    return { vertices: t, indices: e, uvs: r };
  const s = Math.max(2, 16 - i.z);
  return subdivideMesh(t, e, r, 1 / s);
}, subdivideLine = (t, e) => {
  const r = [];
  for (let i2 = 0, s = t.length - 1; i2 < s; i2++) {
    const s2 = t[i2], n = t[i2 + 1], a = getDistance(s2, n), o = Math.ceil(a / e);
    r.push(s2);
    for (let t4 = 1; t4 < o; t4++) {
      const e2 = t4 / o;
      r.push(intepolateArray2(s2, n, e2));
    }
  }
  const i = t[t.length - 1];
  return r.push(i), r;
}, subdivideLineVertices = (t, e) => {
  if (e.sourceProjectionName === e.targetProjectionName)
    return t;
  const r = Math.max(2, 16 - e.z);
  return subdivideLine(t, 1 / r);
}, computeNormals = (t, e = 3, r = 0) => {
  const { vertices: i, indices: s } = t, n = i.length / e, a = new Array(3 * n).fill(0), o = new Array(n).fill(0);
  for (let t4 = 0; t4 < s.length; t4 += 3) {
    const n2 = s[t4], h = s[t4 + 1], l = s[t4 + 2], c = i[n2 * e + r], u = i[n2 * e + r + 1], d = i[n2 * e + r + 2], p = i[h * e + r] - c, f = i[h * e + r + 1] - u, _ = i[h * e + r + 2] - d, y = i[l * e + r] - c, m = i[l * e + r + 1] - u, g = i[l * e + r + 2] - d, x = f * g - _ * m, M = _ * y - p * g, w = p * m - f * y;
    a[3 * n2] += x, a[3 * n2 + 1] += M, a[3 * n2 + 2] += w, o[n2]++, a[3 * h] += x, a[3 * h + 1] += M, a[3 * h + 2] += w, o[h]++, a[3 * l] += x, a[3 * l + 1] += M, a[3 * l + 2] += w, o[l]++;
  }
  for (let t4 = 0; t4 < n; t4++) {
    const e2 = o[t4];
    if (e2 > 0) {
      let r2 = a[3 * t4] / e2, i2 = a[3 * t4 + 1] / e2, s2 = a[3 * t4 + 2] / e2;
      const n2 = Math.sqrt(r2 * r2 + i2 * i2 + s2 * s2);
      if (n2 > 0) {
        const t5 = 1 / n2;
        r2 *= t5, i2 *= t5, s2 *= t5;
      } else
        r2 = 0, i2 = 0, s2 = 1;
      a[3 * t4] = r2, a[3 * t4 + 1] = i2, a[3 * t4 + 2] = s2;
    }
  }
  t.normals = a;
}, createGroundTileMesh = (t, e, r = true) => {
  const { vertices: i, uvs: s, indices: n } = subdivideVertices([-0.5, -0.5, 0, -0.5, 0.5, 0, 0.5, 0.5, 0, 0.5, -0.5, 0], [0, 2, 1, 0, 3, 2], [0, 0, 0, 1, 1, 1, 1, 0], t);
  projectVertices(i, t);
  const a = { vertices: i, uvs: s, indices: n };
  if (e) {
    const t4 = i.length / 3;
    for (const r2 of Object.keys(e)) {
      const i2 = e[r2], s2 = Array.isArray(i2), n2 = s2 ? i2.length : 1, o = [];
      if (s2)
        for (let e2 = 0; e2 < t4; e2++)
          for (let t5 = 0; t5 < n2; t5++)
            o.push(i2[t5]);
      else
        for (let e2 = 0; e2 < t4; e2++)
          o.push(i2);
      a[r2] = o;
    }
  }
  return r && computeNormals(a), a;
}, LAYER_INDEX = 1e-3;
new Vector3$1(), new Vector3$1();
class BackGroundExecuter {
  constructor(t, e, r) {
    __publicField(this, "_styleLayer");
    __publicField(this, "_mapboxStyle");
    __publicField(this, "_zoom");
    __publicField(this, "_color");
    __publicField(this, "_colors", []);
    __publicField(this, "_vertices", []);
    __publicField(this, "_uvs", []);
    __publicField(this, "_indices", []);
    __publicField(this, "_normals", []);
    __publicField(this, "_layerIndices", []);
    this._styleLayer = t, this._zoom = e, this._mapboxStyle = r;
  }
  execute() {
    this._getBgColor(), this._setupBackgroundData();
  }
  getExecutedData() {
    return { colors: this._colors, vertices: this._vertices, uvs: this._uvs, indices: this._indices, normals: this._normals, layerIndices: this._layerIndices };
  }
  _getStyleValue(t, e) {
    return this._mapboxStyle.getValue(this._styleLayer, t, e, this._zoom, {});
  }
  _getBgColor() {
    const t = this._getStyleValue("paint", "background-color");
    this._color = { r: t.r, g: t.g, b: t.b };
    const e = this._getStyleValue("paint", "background-opacity");
    this._color.a = e;
  }
  _setupBackgroundData() {
    const t = this._color, e = createGroundTileMesh(this.tileConfig, { colors: [t.r, t.g, t.b, t.a], layerIndices: LAYER_INDEX });
    this._vertices = new Float32Array(e.vertices), this._uvs = new Float32Array(e.uvs), this._indices = new Uint32Array(e.indices), this._colors = new Float32Array(e.colors), this._normals = new Float32Array(e.normals), this._layerIndices = new Float32Array(e.layerIndices);
  }
}
function quickselect(t, e, r, i, s) {
  quickselectStep(t, e, r || 0, i || t.length - 1, s || defaultCompare);
}
function quickselectStep(t, e, r, i, s) {
  for (; i > r; ) {
    if (i - r > 600) {
      var n = i - r + 1, a = e - r + 1, o = Math.log(n), h = 0.5 * Math.exp(2 * o / 3), l = 0.5 * Math.sqrt(o * h * (n - h) / n) * (a - n / 2 < 0 ? -1 : 1);
      quickselectStep(t, e, Math.max(r, Math.floor(e - a * h / n + l)), Math.min(i, Math.floor(e + (n - a) * h / n + l)), s);
    }
    var c = t[e], u = r, d = i;
    for (swap(t, r, e), s(t[i], c) > 0 && swap(t, r, i); u < d; ) {
      for (swap(t, u, d), u++, d--; s(t[u], c) < 0; )
        u++;
      for (; s(t[d], c) > 0; )
        d--;
    }
    0 === s(t[r], c) ? swap(t, r, d) : swap(t, ++d, i), d <= e && (r = d + 1), e <= d && (i = d - 1);
  }
}
function swap(t, e, r) {
  var i = t[e];
  t[e] = t[r], t[r] = i;
}
function defaultCompare(t, e) {
  return t < e ? -1 : t > e ? 1 : 0;
}
function colorToArr4(t) {
  if (Array.isArray(t))
    return 4 === t.length ? t : [t[0] || 0, t[1] || 0, t[2] || 0, void 0 !== t[3] ? t[3] : 1];
  let e = convertSRGBColor(t).toArray(), r = void 0 === e[3] ? 1 : e[3];
  return [e[0], e[1], e[2], r];
}
function convertSRGBColor(t) {
  if (!t)
    return new Color$1(1, 1, 1);
  if (t.isColor)
    return t;
  if ("string" == typeof t) {
    const e = /^#([0-9A-Fa-f]{8})/, r = t.match(e);
    if (r) {
      const t4 = r[1], e2 = `#${t4.slice(0, 6)}`, i2 = parseInt(t4.slice(6, 8), 16) / 255, s2 = new Color$1(e2);
      return new Vector4(s2.r, s2.g, s2.b, i2);
    }
    const i = /rgba\s*\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*([\d.]+)\s*\)/, s = t.match(i);
    if (s) {
      const t4 = parseInt(s[1], 10), e2 = parseInt(s[2], 10), r2 = parseInt(s[3], 10), i2 = parseFloat(s[4]), n2 = new Color$1(`rgb(${t4}, ${e2}, ${r2})`);
      return new Vector4(n2.r, n2.g, n2.b, i2);
    }
    const n = /hsla\s*\(\s*(\d+)\s*,\s*(\d+)%\s*,\s*(\d+)%\s*,\s*([\d.]+)\s*\)/, a = t.match(n);
    if (a) {
      const t4 = parseInt(a[1], 10) / 360, e2 = parseInt(a[2], 10) / 100, r2 = parseInt(a[3], 10) / 100, i2 = parseFloat(a[4]), s2 = new Color$1().setHSL(t4, e2, r2);
      return new Vector4(s2.r, s2.g, s2.b, i2);
    }
  }
  return new Color$1(t);
}
const _Color = class {
  constructor(t, e, r, i) {
    __publicField(this, "r");
    __publicField(this, "g");
    __publicField(this, "b");
    __publicField(this, "a");
    this.r = t, this.g = e, this.b = r, this.a = i;
  }
  static parse(t) {
    if (!t)
      return;
    if (t instanceof _Color)
      return t;
    if ("string" != typeof t)
      return;
    const e = colorToArr4(t);
    return e ? new _Color(e[0], e[1], e[2], e[3]) : void 0;
  }
  toString() {
    const [t, e, r, i] = this.toArray();
    return `rgba(${Math.round(t)},${Math.round(e)},${Math.round(r)},${i})`;
  }
  toArray() {
    const { r: t, g: e, b: r, a: i } = this;
    return 0 === i ? [0, 0, 0, 0] : [255 * t / i, 255 * e / i, 255 * r / i, i];
  }
  toArray01() {
    const { r: t, g: e, b: r, a: i } = this;
    return 0 === i ? [0, 0, 0, 0] : [t / i, e / i, r / i, i];
  }
  toArray01PremultipliedAlpha() {
    const { r: t, g: e, b: r, a: i } = this;
    return [t, e, r, i];
  }
};
let Color = _Color;
__publicField(Color, "black");
__publicField(Color, "white");
__publicField(Color, "transparent");
__publicField(Color, "red");
__publicField(Color, "blue");
Color.black = new Color(0, 0, 0, 1), Color.white = new Color(1, 1, 1, 1), Color.transparent = new Color(0, 0, 0, 0), Color.red = new Color(1, 0, 0, 1), Color.blue = new Color(0, 0, 1, 1);
const interpolate = { number: (t, e, r) => t * (1 - r) + e * r, color(t, e, r) {
  return new Color(this.number(t.r, e.r, r), this.number(t.g, e.g, r), this.number(t.b, e.b, r), this.number(t.a, e.a, r));
}, array(t, e, r) {
  return t.map((t4, i) => this.number(t4, e[i], r));
} }, interpolateNumber = interpolate.number, Xn = 0.95047, Yn = 1, Zn = 1.08883, t0 = 4 / 29, t1 = 6 / 29, t2 = 3 * t1 * t1, t3 = t1 * t1 * t1, deg2rad = Math.PI / 180, rad2deg = 180 / Math.PI;
function xyz2lab(t) {
  return t > t3 ? Math.pow(t, 1 / 3) : t / t2 + t0;
}
function lab2xyz(t) {
  return t > t1 ? t * t * t : t2 * (t - t0);
}
function xyz2rgb(t) {
  return 255 * (t <= 31308e-7 ? 12.92 * t : 1.055 * Math.pow(t, 1 / 2.4) - 0.055);
}
function rgb2xyz(t) {
  return (t /= 255) <= 0.04045 ? t / 12.92 : Math.pow((t + 0.055) / 1.055, 2.4);
}
function rgbToLab(t) {
  const e = rgb2xyz(t.r), r = rgb2xyz(t.g), i = rgb2xyz(t.b), s = xyz2lab((0.4124564 * e + 0.3575761 * r + 0.1804375 * i) / Xn), n = xyz2lab((0.2126729 * e + 0.7151522 * r + 0.072175 * i) / Yn);
  return { l: 116 * n - 16, a: 500 * (s - n), b: 200 * (n - xyz2lab((0.0193339 * e + 0.119192 * r + 0.9503041 * i) / Zn)), alpha: t.a };
}
function labToRgb(t) {
  let e = (t.l + 16) / 116, r = isNaN(t.a) ? e : e + t.a / 500, i = isNaN(t.b) ? e : e - t.b / 200;
  return e = Yn * lab2xyz(e), r = Xn * lab2xyz(r), i = Zn * lab2xyz(i), new Color(xyz2rgb(3.2404542 * r - 1.5371385 * e - 0.4985314 * i), xyz2rgb(-0.969266 * r + 1.8760108 * e + 0.041556 * i), xyz2rgb(0.0556434 * r - 0.2040259 * e + 1.0572252 * i), t.alpha);
}
function interpolateLab(t, e, r) {
  return { l: interpolateNumber(t.l, e.l, r), a: interpolateNumber(t.a, e.a, r), b: interpolateNumber(t.b, e.b, r), alpha: interpolateNumber(t.alpha, e.alpha, r) };
}
function rgbToHcl(t) {
  const { l: e, a: r, b: i } = rgbToLab(t), s = Math.atan2(i, r) * rad2deg;
  return { h: s < 0 ? s + 360 : s, c: Math.sqrt(r * r + i * i), l: e, alpha: t.a };
}
function hclToRgb(t) {
  const e = t.h * deg2rad, r = t.c;
  return labToRgb({ l: t.l, a: Math.cos(e) * r, b: Math.sin(e) * r, alpha: t.alpha });
}
function interpolateHue(t, e, r) {
  const i = e - t;
  return t + r * (i > 180 || i < -180 ? i - 360 * Math.round(i / 360) : i);
}
function interpolateHcl(t, e, r) {
  return { h: interpolateHue(t.h, e.h, r), c: interpolateNumber(t.c, e.c, r), l: interpolateNumber(t.l, e.l, r), alpha: interpolateNumber(t.alpha, e.alpha, r) };
}
function unbundle(t) {
  return t instanceof Number || t instanceof String || t instanceof Boolean ? t.valueOf() : t;
}
function deepUnbundle(t) {
  if (Array.isArray(t))
    return t.map(deepUnbundle);
  if (t instanceof Object && !(t instanceof Number || t instanceof String || t instanceof Boolean)) {
    const e = {};
    for (const r in t)
      e[r] = deepUnbundle(t[r]);
    return e;
  }
  return unbundle(t);
}
const lab = { forward: rgbToLab, reverse: labToRgb, interpolate: interpolateLab }, hcl = { forward: rgbToHcl, reverse: hclToRgb, interpolate: interpolateHcl };
function classifyRings(t, e) {
  const r = t.length;
  if (r <= 1)
    return [t];
  const i = [];
  let s, n;
  for (let e2 = 0; e2 < r; e2++) {
    const r2 = calculateSignedArea(t[e2]);
    0 !== r2 && (t[e2].area = Math.abs(r2), void 0 === n && (n = r2 < 0), n === r2 < 0 ? (s && i.push(s), s = [t[e2]]) : s.push(t[e2]));
  }
  if (s && i.push(s), e > 1)
    for (let t4 = 0; t4 < i.length; t4++)
      i[t4].length <= e || (quickselect(i[t4], e, 1, i[t4].length - 1, compareAreas), i[t4] = i[t4].slice(0, e));
  return i;
}
function compareAreas(t, e) {
  return e.area - t.area;
}
function calculateSignedArea(t) {
  let e = 0;
  for (let r, i, s = 0, n = t.length, a = n - 1; s < n; a = s++)
    r = t[s], i = t[a], e += (i.x - r.x) * (r.y + i.y);
  return e;
}
var earcut$1 = { exports: {} };
function earcut(t, e, r) {
  r = r || 2;
  var i, s, n, a, o, h, l, c = e && e.length, u = c ? e[0] * r : t.length, d = linkedList(t, 0, u, r, true), p = [];
  if (!d || d.next === d.prev)
    return p;
  if (c && (d = eliminateHoles(t, e, d, r)), t.length > 80 * r) {
    i = n = t[0], s = a = t[1];
    for (var f = r; f < u; f += r)
      (o = t[f]) < i && (i = o), (h = t[f + 1]) < s && (s = h), o > n && (n = o), h > a && (a = h);
    l = 0 !== (l = Math.max(n - i, a - s)) ? 32767 / l : 0;
  }
  return earcutLinked(d, p, r, i, s, l, 0), p;
}
function linkedList(t, e, r, i, s) {
  var n, a;
  if (s === signedArea(t, e, r, i) > 0)
    for (n = e; n < r; n += i)
      a = insertNode(n, t[n], t[n + 1], a);
  else
    for (n = r - i; n >= e; n -= i)
      a = insertNode(n, t[n], t[n + 1], a);
  return a && equals(a, a.next) && (removeNode(a), a = a.next), a;
}
function filterPoints(t, e) {
  if (!t)
    return t;
  e || (e = t);
  var r, i = t;
  do {
    if (r = false, i.steiner || !equals(i, i.next) && 0 !== area(i.prev, i, i.next))
      i = i.next;
    else {
      if (removeNode(i), (i = e = i.prev) === i.next)
        break;
      r = true;
    }
  } while (r || i !== e);
  return e;
}
function earcutLinked(t, e, r, i, s, n, a) {
  if (t) {
    !a && n && indexCurve(t, i, s, n);
    for (var o, h, l = t; t.prev !== t.next; )
      if (o = t.prev, h = t.next, n ? isEarHashed(t, i, s, n) : isEar(t))
        e.push(o.i / r | 0), e.push(t.i / r | 0), e.push(h.i / r | 0), removeNode(t), t = h.next, l = h.next;
      else if ((t = h) === l) {
        a ? 1 === a ? earcutLinked(t = cureLocalIntersections(filterPoints(t), e, r), e, r, i, s, n, 2) : 2 === a && splitEarcut(t, e, r, i, s, n) : earcutLinked(filterPoints(t), e, r, i, s, n, 1);
        break;
      }
  }
}
function isEar(t) {
  var e = t.prev, r = t, i = t.next;
  if (area(e, r, i) >= 0)
    return false;
  for (var s = e.x, n = r.x, a = i.x, o = e.y, h = r.y, l = i.y, c = s < n ? s < a ? s : a : n < a ? n : a, u = o < h ? o < l ? o : l : h < l ? h : l, d = s > n ? s > a ? s : a : n > a ? n : a, p = o > h ? o > l ? o : l : h > l ? h : l, f = i.next; f !== e; ) {
    if (f.x >= c && f.x <= d && f.y >= u && f.y <= p && pointInTriangle(s, o, n, h, a, l, f.x, f.y) && area(f.prev, f, f.next) >= 0)
      return false;
    f = f.next;
  }
  return true;
}
function isEarHashed(t, e, r, i) {
  var s = t.prev, n = t, a = t.next;
  if (area(s, n, a) >= 0)
    return false;
  for (var o = s.x, h = n.x, l = a.x, c = s.y, u = n.y, d = a.y, p = o < h ? o < l ? o : l : h < l ? h : l, f = c < u ? c < d ? c : d : u < d ? u : d, _ = o > h ? o > l ? o : l : h > l ? h : l, y = c > u ? c > d ? c : d : u > d ? u : d, m = zOrder(p, f, e, r, i), g = zOrder(_, y, e, r, i), x = t.prevZ, M = t.nextZ; x && x.z >= m && M && M.z <= g; ) {
    if (x.x >= p && x.x <= _ && x.y >= f && x.y <= y && x !== s && x !== a && pointInTriangle(o, c, h, u, l, d, x.x, x.y) && area(x.prev, x, x.next) >= 0)
      return false;
    if (x = x.prevZ, M.x >= p && M.x <= _ && M.y >= f && M.y <= y && M !== s && M !== a && pointInTriangle(o, c, h, u, l, d, M.x, M.y) && area(M.prev, M, M.next) >= 0)
      return false;
    M = M.nextZ;
  }
  for (; x && x.z >= m; ) {
    if (x.x >= p && x.x <= _ && x.y >= f && x.y <= y && x !== s && x !== a && pointInTriangle(o, c, h, u, l, d, x.x, x.y) && area(x.prev, x, x.next) >= 0)
      return false;
    x = x.prevZ;
  }
  for (; M && M.z <= g; ) {
    if (M.x >= p && M.x <= _ && M.y >= f && M.y <= y && M !== s && M !== a && pointInTriangle(o, c, h, u, l, d, M.x, M.y) && area(M.prev, M, M.next) >= 0)
      return false;
    M = M.nextZ;
  }
  return true;
}
function cureLocalIntersections(t, e, r) {
  var i = t;
  do {
    var s = i.prev, n = i.next.next;
    !equals(s, n) && intersects(s, i, i.next, n) && locallyInside(s, n) && locallyInside(n, s) && (e.push(s.i / r | 0), e.push(i.i / r | 0), e.push(n.i / r | 0), removeNode(i), removeNode(i.next), i = t = n), i = i.next;
  } while (i !== t);
  return filterPoints(i);
}
function splitEarcut(t, e, r, i, s, n) {
  var a = t;
  do {
    for (var o = a.next.next; o !== a.prev; ) {
      if (a.i !== o.i && isValidDiagonal(a, o)) {
        var h = splitPolygon(a, o);
        return a = filterPoints(a, a.next), h = filterPoints(h, h.next), earcutLinked(a, e, r, i, s, n, 0), void earcutLinked(h, e, r, i, s, n, 0);
      }
      o = o.next;
    }
    a = a.next;
  } while (a !== t);
}
function eliminateHoles(t, e, r, i) {
  var s, n, a, o = [];
  for (s = 0, n = e.length; s < n; s++)
    (a = linkedList(t, e[s] * i, s < n - 1 ? e[s + 1] * i : t.length, i, false)) === a.next && (a.steiner = true), o.push(getLeftmost(a));
  for (o.sort(compareX), s = 0; s < o.length; s++)
    r = eliminateHole(o[s], r);
  return r;
}
function compareX(t, e) {
  return t.x - e.x;
}
function eliminateHole(t, e) {
  var r = findHoleBridge(t, e);
  if (!r)
    return e;
  var i = splitPolygon(r, t);
  return filterPoints(i, i.next), filterPoints(r, r.next);
}
function findHoleBridge(t, e) {
  var r, i = e, s = t.x, n = t.y, a = -1 / 0;
  do {
    if (n <= i.y && n >= i.next.y && i.next.y !== i.y) {
      var o = i.x + (n - i.y) * (i.next.x - i.x) / (i.next.y - i.y);
      if (o <= s && o > a && (a = o, r = i.x < i.next.x ? i : i.next, o === s))
        return r;
    }
    i = i.next;
  } while (i !== e);
  if (!r)
    return null;
  var h, l = r, c = r.x, u = r.y, d = 1 / 0;
  i = r;
  do {
    s >= i.x && i.x >= c && s !== i.x && pointInTriangle(n < u ? s : a, n, c, u, n < u ? a : s, n, i.x, i.y) && (h = Math.abs(n - i.y) / (s - i.x), locallyInside(i, t) && (h < d || h === d && (i.x > r.x || i.x === r.x && sectorContainsSector(r, i))) && (r = i, d = h)), i = i.next;
  } while (i !== l);
  return r;
}
function sectorContainsSector(t, e) {
  return area(t.prev, t, e.prev) < 0 && area(e.next, t, t.next) < 0;
}
function indexCurve(t, e, r, i) {
  var s = t;
  do {
    0 === s.z && (s.z = zOrder(s.x, s.y, e, r, i)), s.prevZ = s.prev, s.nextZ = s.next, s = s.next;
  } while (s !== t);
  s.prevZ.nextZ = null, s.prevZ = null, sortLinked(s);
}
function sortLinked(t) {
  var e, r, i, s, n, a, o, h, l = 1;
  do {
    for (r = t, t = null, n = null, a = 0; r; ) {
      for (a++, i = r, o = 0, e = 0; e < l && (o++, i = i.nextZ); e++)
        ;
      for (h = l; o > 0 || h > 0 && i; )
        0 !== o && (0 === h || !i || r.z <= i.z) ? (s = r, r = r.nextZ, o--) : (s = i, i = i.nextZ, h--), n ? n.nextZ = s : t = s, s.prevZ = n, n = s;
      r = i;
    }
    n.nextZ = null, l *= 2;
  } while (a > 1);
  return t;
}
function zOrder(t, e, r, i, s) {
  return (t = 1431655765 & ((t = 858993459 & ((t = 252645135 & ((t = 16711935 & ((t = (t - r) * s | 0) | t << 8)) | t << 4)) | t << 2)) | t << 1)) | (e = 1431655765 & ((e = 858993459 & ((e = 252645135 & ((e = 16711935 & ((e = (e - i) * s | 0) | e << 8)) | e << 4)) | e << 2)) | e << 1)) << 1;
}
function getLeftmost(t) {
  var e = t, r = t;
  do {
    (e.x < r.x || e.x === r.x && e.y < r.y) && (r = e), e = e.next;
  } while (e !== t);
  return r;
}
function pointInTriangle(t, e, r, i, s, n, a, o) {
  return (s - a) * (e - o) >= (t - a) * (n - o) && (t - a) * (i - o) >= (r - a) * (e - o) && (r - a) * (n - o) >= (s - a) * (i - o);
}
function isValidDiagonal(t, e) {
  return t.next.i !== e.i && t.prev.i !== e.i && !intersectsPolygon(t, e) && (locallyInside(t, e) && locallyInside(e, t) && middleInside(t, e) && (area(t.prev, t, e.prev) || area(t, e.prev, e)) || equals(t, e) && area(t.prev, t, t.next) > 0 && area(e.prev, e, e.next) > 0);
}
function area(t, e, r) {
  return (e.y - t.y) * (r.x - e.x) - (e.x - t.x) * (r.y - e.y);
}
function equals(t, e) {
  return t.x === e.x && t.y === e.y;
}
function intersects(t, e, r, i) {
  var s = sign$1(area(t, e, r)), n = sign$1(area(t, e, i)), a = sign$1(area(r, i, t)), o = sign$1(area(r, i, e));
  return s !== n && a !== o || (!(0 !== s || !onSegment(t, r, e)) || (!(0 !== n || !onSegment(t, i, e)) || (!(0 !== a || !onSegment(r, t, i)) || !(0 !== o || !onSegment(r, e, i)))));
}
function onSegment(t, e, r) {
  return e.x <= Math.max(t.x, r.x) && e.x >= Math.min(t.x, r.x) && e.y <= Math.max(t.y, r.y) && e.y >= Math.min(t.y, r.y);
}
function sign$1(t) {
  return t > 0 ? 1 : t < 0 ? -1 : 0;
}
function intersectsPolygon(t, e) {
  var r = t;
  do {
    if (r.i !== t.i && r.next.i !== t.i && r.i !== e.i && r.next.i !== e.i && intersects(r, r.next, t, e))
      return true;
    r = r.next;
  } while (r !== t);
  return false;
}
function locallyInside(t, e) {
  return area(t.prev, t, t.next) < 0 ? area(t, e, t.next) >= 0 && area(t, t.prev, e) >= 0 : area(t, e, t.prev) < 0 || area(t, t.next, e) < 0;
}
function middleInside(t, e) {
  var r = t, i = false, s = (t.x + e.x) / 2, n = (t.y + e.y) / 2;
  do {
    r.y > n != r.next.y > n && r.next.y !== r.y && s < (r.next.x - r.x) * (n - r.y) / (r.next.y - r.y) + r.x && (i = !i), r = r.next;
  } while (r !== t);
  return i;
}
function splitPolygon(t, e) {
  var r = new Node(t.i, t.x, t.y), i = new Node(e.i, e.x, e.y), s = t.next, n = e.prev;
  return t.next = e, e.prev = t, r.next = s, s.prev = r, i.next = r, r.prev = i, n.next = i, i.prev = n, i;
}
function insertNode(t, e, r, i) {
  var s = new Node(t, e, r);
  return i ? (s.next = i.next, s.prev = i, i.next.prev = s, i.next = s) : (s.prev = s, s.next = s), s;
}
function removeNode(t) {
  t.next.prev = t.prev, t.prev.next = t.next, t.prevZ && (t.prevZ.nextZ = t.nextZ), t.nextZ && (t.nextZ.prevZ = t.prevZ);
}
function Node(t, e, r) {
  this.i = t, this.x = e, this.y = r, this.prev = null, this.next = null, this.z = 0, this.prevZ = null, this.nextZ = null, this.steiner = false;
}
function signedArea(t, e, r, i) {
  for (var s = 0, n = e, a = r - i; n < r; n += i)
    s += (t[a] - t[n]) * (t[n + 1] + t[a + 1]), a = n;
  return s;
}
function isFunction(t) {
  return "object" == typeof t && null !== t && !Array.isArray(t);
}
function assert(t) {
  if (!t)
    throw new Error("The expression evaluated to a falsy value");
}
function supportsPropertyExpression(t) {
  return "data-driven" === t["property-type"];
}
function supportsZoomExpression(t) {
  return !!t.expression && t.expression.parameters.indexOf("zoom") > -1;
}
function supportsInterpolation(t) {
  return !!t.expression && t.expression.interpolated;
}
function fromTemplate(t, e) {
  return t.replace(/\{[^{}}]*\}/g, function(t4) {
    return e[t4.slice(1, -1)] || "";
  });
}
earcut$1.exports = earcut, earcut$1.exports.default = earcut, earcut.deviation = function(t, e, r, i) {
  var s = e && e.length, n = s ? e[0] * r : t.length, a = Math.abs(signedArea(t, 0, n, r));
  if (s)
    for (var o = 0, h = e.length; o < h; o++) {
      var l = e[o] * r, c = o < h - 1 ? e[o + 1] * r : t.length;
      a -= Math.abs(signedArea(t, l, c, r));
    }
  var u = 0;
  for (o = 0; o < i.length; o += 3) {
    var d = i[o] * r, p = i[o + 1] * r, f = i[o + 2] * r;
    u += Math.abs((t[d] - t[f]) * (t[p + 1] - t[d + 1]) - (t[d] - t[p]) * (t[f + 1] - t[d + 1]));
  }
  return 0 === a && 0 === u ? 0 : Math.abs((u - a) / a);
}, earcut.flatten = function(t) {
  for (var e = t[0][0].length, r = { vertices: [], holes: [], dimensions: e }, i = 0, s = 0; s < t.length; s++) {
    for (var n = 0; n < t[s].length; n++)
      for (var a = 0; a < e; a++)
        r.vertices.push(t[s][n][a]);
    s > 0 && (i += t[s - 1].length, r.holes.push(i));
  }
  return r;
};
const EARCUT_MAX_RINGS$1 = 500;
class FillExecuter {
  constructor(t, e, r, i) {
    __publicField(this, "_sourceLayer");
    __publicField(this, "_mapboxStyle");
    __publicField(this, "_feature");
    __publicField(this, "_styleLayer");
    __publicField(this, "_color");
    __publicField(this, "_iconData");
    __publicField(this, "_colors_water", []);
    __publicField(this, "_vertices_water", []);
    __publicField(this, "_uvs_water", []);
    __publicField(this, "_indices_water", []);
    __publicField(this, "_normals_water", []);
    __publicField(this, "_layerIndices_water", []);
    __publicField(this, "_colors_wood", []);
    __publicField(this, "_vertices_wood", []);
    __publicField(this, "_uvs_wood", []);
    __publicField(this, "_indices_wood", []);
    __publicField(this, "_normals_wood", []);
    __publicField(this, "_layerIndices_wood", []);
    __publicField(this, "_colors_opaque", []);
    __publicField(this, "_vertices_opaque", []);
    __publicField(this, "_uvs_opaque", []);
    __publicField(this, "_indices_opaque", []);
    __publicField(this, "_normals_opaque", []);
    __publicField(this, "_layerIndices_opaque", []);
    __publicField(this, "_colors_translucent", []);
    __publicField(this, "_vertices_translucent", []);
    __publicField(this, "_uvs_translucent", []);
    __publicField(this, "_indices_translucent", []);
    __publicField(this, "_normals_translucent", []);
    __publicField(this, "_layerIndices_translucent", []);
    __publicField(this, "_colors_pattern", []);
    __publicField(this, "_vertices_pattern", []);
    __publicField(this, "_uvs_pattern", []);
    __publicField(this, "_indices_pattern", []);
    __publicField(this, "_normals_pattern", []);
    __publicField(this, "_layerIndices_pattern", []);
    __publicField(this, "_icons_pattern", []);
    this._feature = t, this._styleLayer = e, this._sourceLayer = r, this._mapboxStyle = i;
  }
  execute(t) {
    this._getFillPattern(), this._getFillColor(), this._loadGeometry(t);
  }
  getExecutedData() {
    return { colors_water: this._colors_water, vertices_water: this._vertices_water, uvs_water: this._uvs_water, indices_water: this._indices_water, normals_water: this._normals_water, layerIndices_water: this._layerIndices_water, colors_wood: this._colors_wood, vertices_wood: this._vertices_wood, uvs_wood: this._uvs_wood, indices_wood: this._indices_wood, normals_wood: this._normals_wood, layerIndices_wood: this._layerIndices_wood, colors_opaque: this._colors_opaque, vertices_opaque: this._vertices_opaque, uvs_opaque: this._uvs_opaque, indices_opaque: this._indices_opaque, normals_opaque: this._normals_opaque, layerIndices_opaque: this._layerIndices_opaque, colors_translucent: this._colors_translucent, vertices_translucent: this._vertices_translucent, uvs_translucent: this._uvs_translucent, indices_translucent: this._indices_translucent, normals_translucent: this._normals_translucent, layerIndices_translucent: this._layerIndices_translucent, colors_pattern: this._colors_pattern, vertices_pattern: this._vertices_pattern, uvs_pattern: this._uvs_pattern, indices_pattern: this._indices_pattern, normals_pattern: this._normals_pattern, layerIndices_pattern: this._layerIndices_pattern, icons_pattern: this._icons_pattern };
  }
  _getStyleValue(t, e) {
    const r = this._sourceLayer.tileKey.z;
    return this._mapboxStyle.getValue(this._styleLayer, t, e, r, this._feature);
  }
  _getFillColor() {
    const t = this._getStyleValue("paint", "fill-color");
    this._color = { r: t.r, g: t.g, b: t.b };
    const e = this._getStyleValue("paint", "fill-opacity");
    this._color.a = e;
  }
  _getFillTranslate() {
    const t = this._getStyleValue("paint", "fill-translate");
    this._getStyleValue("paint", "fill-translate-anchor"), this._translate = t;
  }
  _loadGeometry(t) {
    const e = this._feature.loadGeometry(), r = this._feature.extent;
    this._sourceLayer.tileSize, this._sourceLayer.tileKey.z;
    const { vertices: i, colors: s, uvs: n, normals: a, indices: o, layerIndices: h, getIndexOffset: l, setIndexOffset: c } = this._getRenderingAttributes(), u = classifyRings(e, EARCUT_MAX_RINGS$1);
    this._sourceLayer.tileKey.x, this._sourceLayer.tileKey.y;
    let d = [];
    const p = defined(this._iconData);
    if (p) {
      const t4 = this._iconData.uvs, e2 = t4[0], r2 = 1 - t4[1];
      d = [e2, r2, t4[4] - e2, 1 - t4[3] - r2];
    }
    for (let e2 = 0; e2 < u.length; e2++) {
      const f = u[e2], _ = [], y = [], m = [];
      for (let t4 = 0; t4 < f.length; t4++) {
        const e3 = f[t4];
        e3 !== f[0] && y.push(_.length / 3);
        for (let t5 = 0; t5 < e3.length; t5++) {
          let i2 = e3[t5].x, s2 = e3[t5].y;
          _.push(i2 / r - 0.5, 1 - s2 / r - 0.5, 0), m.push(i2 / r, 1 - s2 / r);
        }
      }
      const g = earcut$1.exports(_, y, 3), { vertices: x, uvs: M, indices: w } = subdivideVertices(_, g, m, this.tileConfig);
      let b = this._sourceLayer[l]();
      for (let t4 = 0; t4 < w.length; t4++)
        o.push(w[t4] + b);
      for (let e3 = 0, r2 = 0, o2 = x.length - 2; e3 < o2; e3 += 3, r2 += 2)
        i.push(x[e3], x[e3 + 1], x[e3 + 2]), n.push(M[r2], M[r2 + 1]), s.push(this._color.r, this._color.g, this._color.b, this._color.a), a.push(0, 0, 1), h.push(t), p && this._icons_pattern.push(d[0], d[1], d[2], d[3]);
      this._sourceLayer[c](b += x.length / 3);
    }
    projectVertices(i, this.tileConfig);
  }
  _getOutLineColor() {
  }
  _getFillPattern() {
    const t = this._getStyleValue("paint", "fill-pattern");
    if (t) {
      const e = "string" == typeof t ? fromTemplate(t, this._feature.properties) : t.toString(), r = this._mapboxStyle.spriteData[e];
      r && (this._iconData = r);
    }
  }
  _getRenderingAttributes() {
    const t = 1 === this._color.a, e = "water" === this._styleLayer.id, r = "landcover-wood" === this._styleLayer.id, i = defined(this._iconData) ? "pattern" : e ? "water" : r ? "wood" : t ? "opaque" : "translucent", [s, n, a, o, h, l, c] = { water: ["_colors_water", "_vertices_water", "_uvs_water", "_indices_water", "_normals_water", "getFillWaterIndexOffset", "setFillWaterIndexOffset"], wood: ["_colors_wood", "_vertices_wood", "_uvs_wood", "_indices_wood", "_normals_wood", "getFillWoodIndexOffset", "setFillWoodIndexOffset"], opaque: ["_colors_opaque", "_vertices_opaque", "_uvs_opaque", "_indices_opaque", "_normals_opaque", "getFillOpaqueIndexOffset", "setFillOpaqueIndexOffset"], translucent: ["_colors_translucent", "_vertices_translucent", "_uvs_translucent", "_indices_translucent", "_normals_translucent", "getFillTranslucentIndexOffset", "setFillTranslucentIndexOffset"], pattern: ["_colors_pattern", "_vertices_pattern", "_uvs_pattern", "_indices_pattern", "_normals_pattern", "getFillPatternIndexOffset", "setFillPatternIndexOffset"] }[i];
    return { colors: this[s], vertices: this[n], uvs: this[a], indices: this[o], normals: this[h], layerIndices: this[`_layerIndices_${i}`], getIndexOffset: l, setIndexOffset: c };
  }
}
const EARCUT_MAX_RINGS = 500, MERCATOR_LENGTH$1 = 200375083427892e-7;
class FillExtrusionExecuter {
  constructor(t, e, r, i) {
    __publicField(this, "_sourceLayer");
    __publicField(this, "_mapboxStyle");
    __publicField(this, "_feature");
    __publicField(this, "_styleLayer");
    __publicField(this, "_color");
    __publicField(this, "_colors_opaque", []);
    __publicField(this, "_vertices_opaque", []);
    __publicField(this, "_uvs_opaque", []);
    __publicField(this, "_indices_opaque", []);
    __publicField(this, "_indices_opaque_side", []);
    __publicField(this, "_normals_opaque", []);
    __publicField(this, "_layerIndices_opaque", []);
    __publicField(this, "_extent_vertices_opaque", []);
    __publicField(this, "_colors_translucent", []);
    __publicField(this, "_vertices_translucent", []);
    __publicField(this, "_uvs_translucent", []);
    __publicField(this, "_indices_translucent", []);
    __publicField(this, "_indices_translucent_side", []);
    __publicField(this, "_normals_translucent", []);
    __publicField(this, "_layerIndices_translucent", []);
    __publicField(this, "_extent_vertices_translucent", []);
    __publicField(this, "_crossVectors3", (t, e) => {
      const r = [];
      return r[0] = t[1] * e[2] - t[2] * e[1], r[1] = t[2] * e[0] - t[0] * e[2], r[2] = t[0] * e[1] - t[1] * e[0], r;
    });
    __publicField(this, "_getDistance2", (t, e) => Math.sqrt(Math.pow(t[0] - e[0], 2) + Math.pow(t[1] - e[1], 2)));
    this._feature = t, this._styleLayer = e, this._sourceLayer = r, this._mapboxStyle = i;
  }
  execute(t) {
    this._getFillColor(), this._loadGeometry(t);
  }
  getExecutedData() {
    return { colors_opaque: this._colors_opaque, vertices_opaque: this._vertices_opaque, uvs_opaque: this._uvs_opaque, indices_opaque: this._indices_opaque, indices_opaque_side: this._indices_opaque_side, normals_opaque: this._normals_opaque, layerIndices_opaque: this._layerIndices_opaque, colors_translucent: this._colors_translucent, vertices_translucent: this._vertices_translucent, uvs_translucent: this._uvs_translucent, indices_translucent: this._indices_translucent, indices_translucent_side: this._indices_translucent_side, normals_translucent: this._normals_translucent, layerIndices_translucent: this._layerIndices_translucent, extentVertices_opaque: this._extent_vertices_opaque, extentVertices_translucent: this._extent_vertices_translucent };
  }
  _getStyleValue(t, e) {
    const r = this._sourceLayer.tileKey.z;
    return this._mapboxStyle.getValue(this._styleLayer, t, e, r, this._feature);
  }
  _getFillColor() {
    const t = this._getStyleValue("paint", "fill-extrusion-color");
    this._color = { r: t.r, g: t.g, b: t.b }, this._sideColor = { r: 0.6 * t.r, g: 0.6 * t.g, b: 0.6 * t.b };
    const e = this._getStyleValue("paint", "fill-extrusion-opacity");
    this._color.a = e, this._base = this._getStyleValue("paint", "fill-extrusion-base") || 0, this._height = this._getStyleValue("paint", "fill-extrusion-height");
  }
  _loadGeometry(t) {
    const e = this._feature.loadGeometry(), r = this._feature.extent, i = this._sourceLayer.tileSize, s = this._sourceLayer.tileKey.z, n = r / i, a = MERCATOR_LENGTH$1 / i / Math.pow(2, s - 1), { vertices: o, colors: h, uvs: l, normals: c, indices: u, indicesSide: d, layerIndices: p, extentVertices: f, getIndexOffset: _, setIndexOffset: y } = this._getRenderingAttributes(), m = classifyRings(e, EARCUT_MAX_RINGS);
    for (let e2 = 0; e2 < m.length; e2++) {
      const i2 = m[e2], s2 = [], g = [];
      for (let e3 = 0; e3 < i2.length; e3++) {
        const o2 = i2[e3];
        o2 !== i2[0] && g.push(s2.length / 3);
        for (let e4 = 0; e4 < o2.length; e4++) {
          let i3 = o2[e4].x, u2 = o2[e4].y;
          const d2 = i3 / n * a, _2 = -u2 / n * a, y2 = i3 / r, m2 = u2 / r;
          s2.push(y2 - 0.5, 1 - m2 - 0.5, this._height), f.push(y2, m2), l.push(d2, _2), h.push(this._color.r, this._color.g, this._color.b, this._color.a), c.push(0, 0, 1), p.push(t);
        }
      }
      const x = earcut$1.exports(s2, g, 3);
      let M = this._sourceLayer[_]();
      for (let t4 = 0; t4 < x.length; t4++)
        u.push(x[t4] + M);
      o.push(...s2), this._sourceLayer[y](M += s2.length / 3);
      for (let e3 = 0; e3 < i2.length; e3 += 1) {
        const s3 = i2[e3];
        for (let e4 = 0, i3 = s3.length; e4 < s3.length; e4++) {
          let u2 = s3[e4].x / r - 0.5, m2 = 1 - s3[e4].y / r - 0.5, g2 = [u2, m2, this._base], x2 = [u2, m2, this._height], M2 = e4 + 1;
          e4 === i3 - 1 && (M2 = 0);
          let w = s3[M2].x / r - 0.5, b = 1 - s3[M2].y / r - 0.5, v = [w, b, this._base], S = [w, b, this._height], C = [v[0] - g2[0], v[1] - g2[1], v[2] - g2[2]], E = [x2[0] - g2[0], x2[1] - g2[1], x2[2] - g2[2]], P = this._crossVectors3(C, E), T = this._getDistance2(g2, v);
          T *= r / n * a, o.push(g2[0], g2[1], g2[2], x2[0], x2[1], x2[2], v[0], v[1], v[2], S[0], S[1], S[2]), f.push(g2[0] + 0.5, g2[1] + 0.5, x2[0] + 0.5, x2[1] + 0.5, v[0] + 0.5, v[1] + 0.5, S[0] + 0.5, S[1] + 0.5), l.push(0, this._base, 0, this._height, T, this._base, T, this._height), c.push(P[0], P[1], P[2], P[0], P[1], P[2], P[0], P[1], P[2], P[0], P[1], P[2]), h.push(this._sideColor.r, this._sideColor.g, this._sideColor.b, this._color.a, this._sideColor.r, this._sideColor.g, this._sideColor.b, this._color.a, this._sideColor.r, this._sideColor.g, this._sideColor.b, this._color.a, this._sideColor.r, this._sideColor.g, this._sideColor.b, this._color.a), p.push(t, t, t, t);
          let I = this._sourceLayer[_]();
          d.push(I, I + 1, I + 2, I + 2, I + 1, I + 3), this._sourceLayer[y](I += 4);
        }
      }
      projectVertices(o, this.tileConfig);
    }
  }
  _getOutLineColor() {
  }
  _getFillPattern() {
  }
  _getRenderingAttributes() {
    const t = 1 === this._color.a ? "opaque" : "translucent", [e, r, i, s, n, a, o, h, l] = { opaque: ["_colors_opaque", "_vertices_opaque", "_uvs_opaque", "_indices_opaque", "_indices_opaque_side", "_normals_opaque", "_extent_vertices_opaque", "getFillExtrusionOpaqueIndexOffset", "setFillExtrusionOpaqueIndexOffset"], translucent: ["_colors_translucent", "_vertices_translucent", "_uvs_translucent", "_indices_translucent", "_indices_translucent_side", "_normals_translucent", "_extent_vertices_translucent", "getFillExtrusionTranslucentIndexOffset", "setFillExtrusionTranslucentIndexOffset"] }[t];
    return { colors: this[e], vertices: this[r], uvs: this[i], indices: this[s], indicesSide: this[n], normals: this[a], layerIndices: this[`_layerIndices_${t}`], extentVertices: this[o], getIndexOffset: h, setIndexOffset: l };
  }
}
function vector2RotateTo(t, e) {
  let r = t.x * e.x + t.y * e.y, i = Math.sqrt(t.x * t.x + t.y * t.y), s = Math.sqrt(e.x * e.x + e.y * e.y), n = Math.acos(r / (i * s));
  return t.x * e.y - e.x * t.y < 0 && (n = 2 * Math.PI - n), n;
}
const _centerIn = new Vector3$1(), _lastPosition = new Vector3$1(), _currentPosition = new Vector3$1(), _lineNormal = new Vector3$1(), _offset = new Vector3$1(), _segmentLines3D = (t, e, r, i, s, n, a, o, h, l, c, u, d, p, f, _) => {
  const y = d.targetCenter, m = d.targetProjection, { sourceProjectionName: g, targetProjectionName: x } = d;
  let M = 0;
  _centerIn.fromArray(y), _lastPosition.fromArray(a), _currentPosition.fromArray(o);
  let w = u.value;
  s.push(w, w), n.push(w, 1, w, 0);
  const b = m.getProjectedSurfaceNormal(_lastPosition, _lineNormal), v = _offset.copy(_currentPosition).sub(_lastPosition), S = _lineNormal.crossVectors(b, v).normalize();
  g !== x && (_lastPosition.sub(_centerIn), _currentPosition.sub(_centerIn)), r.push(S.x, S.y, S.z, -S.x, -S.y, -S.z), t.push(_lastPosition.x, _lastPosition.y, _lastPosition.z, _lastPosition.x, _lastPosition.y, _lastPosition.z), e.push(h, h), M += 2, c || (i.push(l + 0 - 2, l + 1 - 2, l + 2 - 2), i.push(l + 2 - 2, l + 1 - 2, l + 3 - 2)), i.push(l, l + 1, l + 2), r.push(S.x, S.y, S.z, -S.x, -S.y, -S.z), t.push(_currentPosition.x, _currentPosition.y, _currentPosition.z, _currentPosition.x, _currentPosition.y, _currentPosition.z), e.push(h, h);
  return w += (f && _ ? getDistance(f, _) : getDistance(a, o)) / p, s.push(w, w), n.push(w, 1, w, 0), u.value = w, M += 2, i.push(l + 2, l + 1, l + 3), 4;
};
function lineToMesh(t, e, r, i, s = 1, n) {
  let a = t;
  if (!Array.isArray(t[0])) {
    a = [];
    for (let e2 = 0; e2 < t.length; e2 += 3)
      a.push([t[e2], t[e2 + 1], t[e2 + 2]]);
  }
  let o = 0, h = null, l = null, c = null;
  const u = [], d = [], p = [], f = [], _ = [], y = [];
  let m;
  const g = { value: 0 }, x = n || a;
  let M = true;
  for (let t4 = 1, i2 = a.length - 1; t4 <= i2; t4++) {
    h = c || a[t4 - 1], l = a[t4];
    const i3 = c ? n ? n[a.indexOf(c)] : c : x[t4 - 1], m2 = x[t4], w = x[t4 + 1];
    if (w && isPointEqual(m2, w)) {
      c = h;
      continue;
    }
    let b = 0;
    b = _segmentLines3D(u, f, d, p, _, y, h, l, e, o, M, g, r, s, i3, m2), -1 !== b && (o += b, c = null), M = false;
  }
  for (let t4 = 0; t4 < y.length; t4++)
    t4 % 2 == 0 && (y[t4] = g.value - y[t4]);
  return i && (m = new Array(_.length).fill(g.value)), { vertices: u, indices: p, normals: d, widths: f, lengths: _, totalLengths: m, uvs: y };
}
const PROJECTION_GEO = "EPSG:4326", PROJECTION_WEB_MERCATOR = "EPSG:3857", PROJECTION_ECEF = "EPSG:4978", PROJECTION_BD_MERCATOR = "BD:MERCATOR", PROJECTION_SCREEN_PIXEL = "SCREEN_PIXEL";
function pushInBatches(t, e, r = 1e4) {
  for (let i = 0; i < e.length; i += r)
    t.push(...e.slice(i, i + r));
}
class LineExecuter {
  constructor(t, e, r, i) {
    __publicField(this, "_sourceLayer");
    __publicField(this, "_mapboxStyle");
    __publicField(this, "_feature");
    __publicField(this, "_styleLayer");
    __publicField(this, "_color");
    __publicField(this, "_w");
    __publicField(this, "_colors_opaque", []);
    __publicField(this, "_vertices_opaque", []);
    __publicField(this, "_indices_opaque", []);
    __publicField(this, "_normals_opaque", []);
    __publicField(this, "_widths_opaque", []);
    __publicField(this, "_extent_vertices_opaque", []);
    __publicField(this, "_layerIndices_opaque", []);
    __publicField(this, "_colors_translucent", []);
    __publicField(this, "_vertices_translucent", []);
    __publicField(this, "_indices_translucent", []);
    __publicField(this, "_normals_translucent", []);
    __publicField(this, "_widths_translucent", []);
    __publicField(this, "_extent_vertices_translucent", []);
    __publicField(this, "_layerIndices_translucent", []);
    __publicField(this, "_colors_dashed", []);
    __publicField(this, "_vertices_dashed", []);
    __publicField(this, "_indices_dashed", []);
    __publicField(this, "_normals_dashed", []);
    __publicField(this, "_widths_dashed", []);
    __publicField(this, "_layerIndices_dashed", []);
    __publicField(this, "_lengths_dashed", []);
    __publicField(this, "_totalLength_dashed", []);
    __publicField(this, "_extent_vertices_dashed", []);
    __publicField(this, "_dashArray_dashed", []);
    __publicField(this, "_dashRatio_dashed", []);
    this._feature = t, this._styleLayer = e, this._sourceLayer = r, this._mapboxStyle = i;
  }
  execute(t) {
    this._getLineColor(), this._getLineWidth(), this._getLineDash(), this._loadGeometry(t);
  }
  getExecutedData() {
    return { colors_opaque: this._colors_opaque, vertices_opaque: this._vertices_opaque, indices_opaque: this._indices_opaque, normals_opaque: this._normals_opaque, widths_opaque: this._widths_opaque, layerIndices_opaque: this._layerIndices_opaque, extentVertices_opaque: this._extent_vertices_opaque, colors_translucent: this._colors_translucent, vertices_translucent: this._vertices_translucent, indices_translucent: this._indices_translucent, normals_translucent: this._normals_translucent, widths_translucent: this._widths_translucent, layerIndices_translucent: this._layerIndices_translucent, extentVertices_translucent: this._extent_vertices_translucent, colors_dashed: this._colors_dashed, vertices_dashed: this._vertices_dashed, extentVertices_dashed: this._extent_vertices_dashed, lengths_dashed: this._lengths_dashed, indices_dashed: this._indices_dashed, normals_dashed: this._normals_dashed, widths_dashed: this._widths_dashed, layerIndices_dashed: this._layerIndices_dashed, dashArray_dashed: this._dashArray_dashed, dashRatio_dashed: this._dashRatio_dashed };
  }
  _getStyleValue(t, e) {
    const r = this._sourceLayer.tileKey.z;
    return this._mapboxStyle.getValue(this._styleLayer, t, e, r, this._feature);
  }
  _getLineColor() {
    const t = this._getStyleValue("paint", "line-color");
    this._color = { r: t.r, g: t.g, b: t.b };
    const e = this._getStyleValue("paint", "line-opacity");
    this._color.a = e;
  }
  _getLineWidth() {
    const t = this._getStyleValue("paint", "line-width");
    let e = this._getStyleValue("paint", "line-gap-width") || 0;
    e > 0 && (e += 1), this._w = t + e;
  }
  _getLineDash() {
    const t = this._getStyleValue("paint", "line-dasharray");
    if (this.dasharrayProperty = t, this.isDashed = this.dasharrayProperty && this.dasharrayProperty[1] > 0, this.isDashed) {
      const t4 = this.dasharrayProperty[0], e = t4 + this.dasharrayProperty[1];
      this.dashArray = 4 * e, this.dashRatio = Math.max(t4 / e, 0.02);
    }
  }
  _loadGeometry(t) {
    const e = this._feature.loadGeometry(), r = this._feature.extent, i = 1 === this._color.a, { sourceProjectionName: s, targetProjectionName: n } = this.tileConfig;
    for (const a of e) {
      let e2 = [];
      const o = [];
      for (let t4 = 0; t4 < a.length; t4++) {
        let i2 = a[t4].x, s2 = a[t4].y;
        e2.push([i2 / r - 0.5, 1 - s2 / r - 0.5, 0]);
      }
      e2 = subdivideLineVertices(e2, this.tileConfig);
      for (let t4 = 1; t4 < e2.length; t4++) {
        const [r2, i2] = e2[t4 - 1], [s2, n2] = e2[t4], a2 = r2 + 0.5, h2 = i2 + 0.5, l2 = s2 + 0.5, c2 = n2 + 0.5;
        o.push(a2, h2, a2, h2, l2, c2, l2, c2);
      }
      projectVertices(e2, this.tileConfig, false, false);
      const { vertices: h, indices: l, normals: c, widths: u, lengths: d } = lineToMesh(e2, this._w, this.tileConfig);
      if (d.length > 0 && this.tileConfig.projectedBoundingBox && s === n) {
        const t4 = this.tileConfig.projectedBoundingBox, e3 = t4[3] - t4[0];
        for (let t5 = 0, r2 = d.length; t5 < r2; t5++)
          d[t5] *= e3;
      }
      let p = h.length;
      for (; p; )
        this.isDashed ? (this._colors_dashed.push(this._color.r, this._color.g, this._color.b, this._color.a), this._layerIndices_dashed.push(t), this._dashArray_dashed.push(this.dashArray), this._dashRatio_dashed.push(this.dashRatio)) : i ? (this._colors_opaque.push(this._color.r, this._color.g, this._color.b, this._color.a), this._layerIndices_opaque.push(t)) : (this._colors_translucent.push(this._color.r, this._color.g, this._color.b, this._color.a), this._layerIndices_translucent.push(t)), p -= 3;
      if (this.isDashed) {
        let t4 = this._sourceLayer.getLineDashedIndexOffset();
        for (let e3 = 0; e3 < l.length; e3++)
          this._indices_dashed.push(l[e3] + t4);
        pushInBatches(this._vertices_dashed, h), pushInBatches(this._normals_dashed, c), pushInBatches(this._widths_dashed, u), pushInBatches(this._extent_vertices_dashed, o), pushInBatches(this._lengths_dashed, d), this._sourceLayer.setLineDashedIndexOffset(t4 += h.length / 3);
      } else if (i) {
        let t4 = this._sourceLayer.getLineOpaqueIndexOffset();
        for (let e3 = 0; e3 < l.length; e3++)
          this._indices_opaque.push(l[e3] + t4);
        pushInBatches(this._vertices_opaque, h), pushInBatches(this._normals_opaque, c), pushInBatches(this._widths_opaque, u), pushInBatches(this._extent_vertices_opaque, o), this._sourceLayer.setLineOpaqueIndexOffset(t4 += h.length / 3);
      } else {
        let t4 = this._sourceLayer.getLineTranslucentIndexOffset();
        for (let e3 = 0; e3 < l.length; e3++)
          this._indices_translucent.push(l[e3] + t4);
        pushInBatches(this._vertices_translucent, h), pushInBatches(this._normals_translucent, c), pushInBatches(this._widths_translucent, u), pushInBatches(this._extent_vertices_translucent, o), this._sourceLayer.setLineTranslucentIndexOffset(t4 += h.length / 3);
      }
    }
  }
}
class FormattedSection {
  constructor(t, e, r, i, s) {
    __publicField(this, "text");
    __publicField(this, "image");
    __publicField(this, "scale");
    __publicField(this, "fontStack");
    __publicField(this, "textColor");
    this.text = t.normalize ? t.normalize() : t, this.image = e, this.scale = r, this.fontStack = i, this.textColor = s;
  }
}
class Formatted {
  constructor(t) {
    __publicField(this, "sections");
    this.sections = t;
  }
  static fromString(t) {
    return new Formatted([new FormattedSection(t, null, null, null, null)]);
  }
  isEmpty() {
    return 0 === this.sections.length || !this.sections.some((t) => 0 !== t.text.length || t.image && 0 !== t.image.name.length);
  }
  static factory(t) {
    return t instanceof Formatted ? t : Formatted.fromString(t);
  }
  toString() {
    return 0 === this.sections.length ? "" : this.sections.map((t) => t.text).join("");
  }
  serialize() {
    const t = ["format"];
    for (const e of this.sections) {
      if (e.image) {
        t.push(["image", e.image.name]);
        continue;
      }
      t.push(e.text);
      const r = {};
      e.fontStack && (r["text-font"] = ["literal", e.fontStack.split(",")]), e.scale && (r["font-scale"] = e.scale), e.textColor && (r["text-color"] = ["rgba"].concat(e.textColor.toArray())), t.push(r);
    }
    return t;
  }
}
let fontWeights = { thin: 100, hairline: 100, "ultra-light": 200, "extra-light": 200, light: 300, book: 300, regular: 400, normal: 400, plain: 400, roman: 400, standard: 400, medium: 500, "semi-bold": 600, "demi-bold": 600, bold: 700, "extra-bold": 800, "ultra-bold": 800, heavy: 900, black: 900, "heavy-black": 900, fat: 900, poster: 900, "ultra-black": 950, "extra-black": 950 }, italicRE = /(italic|oblique)$/i;
function getFontWeight(t) {
  Array.isArray(t) || (t = [t]);
  let e = 400, r = false;
  for (let i = 0, s = t.length; i < s; ++i) {
    let s2 = t[i].split(" "), n = s2[s2.length - 1].toLowerCase();
    "normal" === n || "italic" === n || "oblique" === n ? (s2.pop(), n = s2[s2.length - 1].toLowerCase()) : italicRE.test(n) && (n = n.replace(italicRE, ""));
    for (let t4 in fontWeights)
      if (fontWeights.hasOwnProperty(t4)) {
        let i2 = s2.length > 1 ? s2[s2.length - 2].toLowerCase() : "";
        if (n === t4 || n === t4.replace("-", "") || i2 + "-" + n === t4) {
          e = r ? e : fontWeights[t4], r = true;
          break;
        }
      }
    if (r || isNaN(n) || "" === n || (e = parseInt(n, 10), r = true), r)
      break;
  }
  return e;
}
const _pointIn = new Vector3$1(), _pointOut = new Vector3$1(), MERCATOR_LENGTH = 4007501668557849e-8;
class SymbolExecuter {
  constructor(t, e, r, i) {
    __publicField(this, "_sourceLayer");
    __publicField(this, "_mapboxStyle");
    __publicField(this, "_feature");
    __publicField(this, "_styleLayer");
    __publicField(this, "_textObject", { text: "" });
    __publicField(this, "_iconData", {});
    __publicField(this, "_iconStyle", {});
    __publicField(this, "_symbols", []);
    this._feature = t, this._styleLayer = e, this._sourceLayer = r, this._mapboxStyle = i, this._zIndex = e.zIndex;
  }
  execute() {
    const t = this._styleLayer.layout;
    "text-field" in t && this._parseText(), "icon-image" in t && this._parseIcon(), ("" !== this._textObject.text || this._iconData.uvs) && this._loadGeometry();
  }
  getExecutedData() {
    return { symbols: this._symbols };
  }
  _getStyleValue(t, e) {
    const r = this._sourceLayer.tileKey.z;
    return this._mapboxStyle.getValue(this._styleLayer, t, e, r, this._feature);
  }
  _parseText() {
    const t = this._getStyleValue("layout", "text-size"), e = this._getStyleValue("layout", "text-font"), r = this._getStyleValue("layout", "text-line-height"), i = this._getStyleValue("layout", "text-max-width"), s = this._getStyleValue("layout", "text-field"), n = this._getStyleValue("layout", "text-anchor"), a = this._getStyleValue("layout", "text-offset"), o = this._getStyleValue("layout", "text-transform"), h = this._getStyleValue("paint", "text-opacity"), l = this._getStyleValue("paint", "text-halo-width");
    let c = this._getStyleValue("paint", "text-color"), u = this._getStyleValue("paint", "text-halo-color");
    const d = this._getStyleValue("paint", "text-translate");
    c = [255 * c.r, 255 * c.g, 255 * c.b, h], u = [255 * u.r, 255 * u.g, 255 * u.b, u.a * h], this._textObject.text = s instanceof Formatted ? s.toString() : fromTemplate(s, this._feature.properties).trim(), "uppercase" === o ? this._textObject.text = this._textObject.text.toUpperCase() : "lowercase" === o && (this._textObject.text = this._textObject.text.toLowerCase()), this._textObject.fontWeight = e.length > 0 ? Math.max(...e.map((t4) => getFontWeight(t4))) : 400, this._textObject.fontSize = t, this._textObject.fontRgba = c, this._textObject.textAnchor = n, this._textObject.textOffset = a, this._textObject.textTranslate = d, 2 !== this._feature.type && (this._textObject.textMaxWidth = i), l > 0 && (this._textObject.haloRgba = u, this._textObject.haloSize = l), this._textObject.lineHeight = r;
    let p = 0, f = 0;
    1 === this._feature.type && (-1 !== n.indexOf("left") ? p = l : -1 !== n.indexOf("right") && (p = -l)), 0 === n.indexOf("bottom") ? f = -l - 0.5 * (r - 1) * t : 0 === n.indexOf("top") && (f = l + 0.5 * (r - 1) * t), this._textObject.hOffset = p, this._textObject.vOffset = f;
  }
  _parseIcon() {
    const t = this._getStyleValue("layout", "icon-image"), e = this._getStyleValue("paint", "icon-opacity");
    let r;
    r = "string" == typeof t ? fromTemplate(t, this._feature.properties) : t.toString();
    const i = this._mapboxStyle.spriteData[r];
    i && (this._iconData = i), this._iconStyle.opacity = e;
  }
  _isPointInExtent(t, e) {
    const [r, i] = e, s = r / t, n = 1 - i / t;
    return s >= 0 && s <= 1 && n >= 0 && n <= 1;
  }
  _loadGeometry() {
    const t = [], e = [], r = [], i = [], s = [], n = [], a = this._styleLayer.layout;
    this._xyToMercator(t, e, r, i, s, n);
    const { x: o, y: h, z: l } = this._sourceLayer.tileKey, c = `${o},${h},${l}`, u = this._feature.extent;
    for (let o2 = 0; o2 < t.length; o2++) {
      let h2 = t[o2], l2 = e[o2], d = 2 === this._feature.type, p = {}, f = i[o2];
      if ("text-field" in a && !("icon-image" in a)) {
        d = d && !this._feature.properties.ref;
        p = { type: d ? "text_flat" : "text_fix", id: this._feature.id ? this._feature.id + "_" + c : h2.toString() + "-" + this._textObject.text + "_" + c, position: h2, textStyle: { fillRgba: this._textObject.fontRgba, fontSize: this._textObject.fontSize, haloRgba: this._textObject.haloRgba, haloSize: this._textObject.haloSize, fontWeight: this._textObject.fontWeight, hOffset: this._textObject.hOffset, vOffset: this._textObject.vOffset, lineHeight: this._textObject.lineHeight, maxWidth: this._textObject.textMaxWidth }, rotateZ: d ? l2 : 0, rank: this._zIndex, text: this._textObject.text };
      } else
        "icon-image" in a && !("text-field" in a) ? (d && (h2 = r[o2], f = s[o2], l2 = n[o2]), p = { type: d ? "icon_flat_uv" : "icon_uv", id: this._feature.id ? this._feature.id + "_" + c : h2.toString() + "_" + c, position: h2, iconStyle: { width: this._iconData.width / this._iconData.pixelRatio, height: this._iconData.height / this._iconData.pixelRatio, opacity: defined(this._iconStyle.opacity) ? this._iconStyle.opacity : 1 }, rotateZ: d ? l2 : 0, iconUvs: this._iconData.uvs, rank: this._zIndex }) : "icon-image" in a && "text-field" in a && (p = { type: "icon_text_uv", id: this._feature.id ? this._feature.id + "_" + c : h2.toString() + "-" + this._textObject.text + "_" + c, position: h2, iconStyle: { width: this._iconData.width / this._iconData.pixelRatio, height: this._iconData.height / this._iconData.pixelRatio, opacity: defined(this._iconStyle.opacity) ? this._iconStyle.opacity : 1 }, iconUvs: this._iconData.uvs, textStyle: { fillRgba: this._textObject.fontRgba, fontSize: this._textObject.fontSize, textAnchor: this._textObject.textAnchor, textOffset: this._textObject.textOffset, fontWeight: this._textObject.fontWeight, hOffset: this._textObject.hOffset, vOffset: this._textObject.vOffset, haloRgba: this._textObject.haloRgba, haloSize: this._textObject.haloSize, lineHeight: this._textObject.lineHeight, maxWidth: this._textObject.textMaxWidth }, rank: this._zIndex, text: this._textObject.text });
      this._isPointInExtent(u, f) && this._symbols.push(p);
    }
  }
  _getMidPoint(t) {
    const e = t.length;
    let { x: r, y: i } = t[0];
    const s = [0];
    let n = 0;
    for (let a2 = 1; a2 < e; a2++) {
      let { x: e2, y: o2 } = t[a2];
      n += Math.sqrt((e2 - r) * (e2 - r) + (o2 - i) * (o2 - i)), s.push(n), r = e2, i = o2;
    }
    const a = 0.5 * n;
    let o = 0, h = 0;
    for (let t4 = 0; t4 < e - 1; t4++) {
      const e2 = s[t4], r2 = s[t4 + 1];
      if (e2 === a) {
        o = t4, h = 0;
        break;
      }
      if (a > e2 && a <= r2) {
        o = t4, h = (a - e2) / (r2 - e2);
        break;
      }
    }
    const l = t[o], c = t[o + 1], u = Math.atan2(l.y - c.y, c.x - l.x);
    return { x: l.x + (c.x - l.x) * h, y: l.y + (c.y - l.y) * h, radians: u };
  }
  _xyToMercator(t, e, r, i, s, n) {
    const a = this._feature.loadGeometry(), o = this._feature.extent, h = this._sourceLayer.tileSize, l = this._sourceLayer.tileKey.z;
    let c = this._sourceLayer.tileKey.x * o, u = this._sourceLayer.tileKey.y * o;
    const d = o / h, p = MERCATOR_LENGTH / h / Math.pow(2, l), { sourceProjection: f, targetProjection: _, sourceProjectionName: y, targetProjectionName: m } = this.tileConfig, g = y === m;
    for (const o2 of a) {
      let a2 = 0;
      2 === this._feature.type && o2.length > 4 && (a2 = 2);
      let { x: h2, y: l2 } = o2[a2];
      const y2 = (c + h2) / d * p - MERCATOR_LENGTH / 2, m2 = MERCATOR_LENGTH / 2 - (u + l2) / d * p;
      if (g ? t.push([y2, m2, 0]) : (_pointIn.set(y2, m2, 0), reprojectCoordinate(void 0, void 0, f, _, _pointIn, _pointOut), t.push([_pointOut.x, _pointOut.y, _pointOut.z])), i.push([h2, l2]), 2 === this._feature.type) {
        const t4 = this._getMidPoint(o2), e2 = t4.x, i2 = t4.y, a3 = (c + e2) / d * p - MERCATOR_LENGTH / 2, h3 = MERCATOR_LENGTH / 2 - (u + i2) / d * p;
        g ? r.push([a3, h3, 0]) : (_pointIn.set(a3, h3, 0), reprojectCoordinate(void 0, void 0, f, _, _pointIn, _pointOut), r.push([_pointOut.x, _pointOut.y, _pointOut.z])), s.push([e2, i2]), n.push(t4.radians);
      }
      if (2 === this._feature.type) {
        let t4 = vector2RotateTo({ x: o2[a2 + 1].x - o2[a2].x, y: o2[a2 + 1].y - o2[a2].y }, { x: 1, y: 0 });
        e.push(t4);
      } else
        e.push(0);
    }
  }
}
const unsupportTypes = ["circle", "raster", "hillshade", "sky", "heatmap"], LAYER_INDEX_DELTA = 5e-3;
let layerIndex = 0.05;
class Executer {
  constructor(t, e, r, i) {
    __publicField(this, "_sourceLayer");
    __publicField(this, "_filterCache", {});
    __publicField(this, "_mapboxStyle");
    __publicField(this, "_displayOptions", {});
    __publicField(this, "_background", { colors: [], vertices: [], uvs: [], normals: [], indices: [], layerIndices: [] });
    __publicField(this, "_fill_opaque", { colors: 0, vertices: 0, uvs: 0, normals: 0, indices: 0, layerIndices: 0 });
    __publicField(this, "_fill_translucent", { colors: 0, vertices: 0, uvs: 0, normals: 0, indices: 0, layerIndices: 0 });
    __publicField(this, "_fill_water", { colors: 0, vertices: 0, uvs: 0, normals: 0, indices: 0, layerIndices: 0 });
    __publicField(this, "_fill_wood", { colors: 0, vertices: 0, uvs: 0, normals: 0, indices: 0, layerIndices: 0 });
    __publicField(this, "_fill_pattern", { colors: 0, vertices: 0, uvs: 0, normals: 0, indices: 0, layerIndices: 0, icons: 0 });
    __publicField(this, "_fill_extrusion_opaque", { colors: 0, vertices: 0, uvs: 0, normals: 0, indices: 0, indices_side: 0, layerIndices: 0, extentVertices: 0 });
    __publicField(this, "_fill_extrusion_translucent", { colors: 0, vertices: 0, uvs: 0, normals: 0, indices: 0, indices_side: 0, layerIndices: 0, extentVertices: 0 });
    __publicField(this, "_line_opaque", { colors: 0, vertices: 0, normals: 0, indices: 0, widths: 0, layerIndices: 0, extentVertices: 0 });
    __publicField(this, "_line_dashed", { colors: 0, vertices: 0, normals: 0, indices: 0, widths: 0, layerIndices: 0, extentVertices: 0, lengths: 0, dashArrays: 0, dashRatios: 0 });
    __publicField(this, "_line_translucent", { colors: 0, vertices: 0, normals: 0, indices: 0, widths: 0, layerIndices: 0, extentVertices: 0 });
    __publicField(this, "_symbol", { symbols: [] });
    __publicField(this, "_fillOpaqueArray", []);
    __publicField(this, "_fillTranslucentArray", []);
    __publicField(this, "_fillWaterArray", []);
    __publicField(this, "_fillWoodArray", []);
    __publicField(this, "_fillExtrusionOpaqueArray", []);
    __publicField(this, "_fillExtrusionTranslucentArray", []);
    __publicField(this, "_fillPatternArray", []);
    __publicField(this, "_lineOpaqueArray", []);
    __publicField(this, "_lineTranslucentArray", []);
    __publicField(this, "_lineDashedArray", []);
    this._sourceLayer = t, this._mapboxStyle = e, this._tileConfig = i, r && (this._displayOptions = r), layerIndex = 0.05;
  }
  execute() {
    const t = this._sourceLayer.tileKey.z, e = this._mapboxStyle._styleJSON.layers;
    for (let r = 0; r < e.length; r++) {
      const i = e[r];
      if (i.zIndex = r, "background" === i.type && false !== this._displayOptions.background) {
        this._setupBackgroundLayer(i, t);
        continue;
      }
      const s = i.id, n = i.filter, a = i["source-layer"], o = this._sourceLayer.getTileLayer(a);
      if (o) {
        for (let e2 = 0; e2 < o.length; e2++) {
          const r2 = o.feature(e2), a2 = this._mapboxStyle.evaluateFilter(s, n, r2, t);
          if (!n || a2) {
            if ("none" === (i.layout || {}).visibility || "minzoom" in i && t < i.minzoom || "maxzoom" in i && t >= i.maxzoom)
              continue;
            const e3 = this._getConstructor(r2, i);
            if (!e3)
              continue;
            const s2 = new e3(r2, i, this._sourceLayer, this._mapboxStyle);
            s2.tileConfig = this._tileConfig, s2.execute(layerIndex);
            const n2 = s2.getExecutedData();
            this._mergeArray(s2, n2);
          }
        }
        layerIndex += LAYER_INDEX_DELTA;
      }
    }
  }
  getExecutedData(t) {
    return this._generatorBufferArray("_background"), t.fill_opaque = this._generatorBufferArray("_fill_opaque"), t.fill_translucent = this._generatorBufferArray("_fill_translucent"), t.fill_water = this._generatorBufferArray("_fill_water"), t.fill_wood = this._generatorBufferArray("_fill_wood"), t.fill_pattern = this._generatorBufferArray("_fill_pattern"), t.fill_extrusion_opaque = this._generatorBufferArray("_fill_extrusion_opaque"), t.fill_extrusion_translucent = this._generatorBufferArray("_fill_extrusion_translucent"), t.line_opaque = this._generatorBufferArray("_line_opaque"), t.line_translucent = this._generatorBufferArray("_line_translucent"), t.line_dashed = this._generatorBufferArray("_line_dashed"), t.background = this._background, t.symbol = { symbols: this._symbol.symbols }, layerIndex;
  }
  _flatArray(t, e, r) {
    for (let i = 0; i < e.length; i++)
      t[r + i] = e[i];
  }
  _generatorBufferArray(t) {
    if ("_background" === t)
      return this._background.vertices = new Float32Array(this._background.vertices), this._background.vertices = new Float32Array(this._background.vertices), this._background.colors = new Float32Array(this._background.colors), this._background.normals = new Float32Array(this._background.normals), this._background.layerIndices = new Float32Array(this._background.layerIndices), void (this._background.indices = new Uint32Array(this._background.indices));
    let e = null, r = null, i = { vertices: new Float32Array(), colors: new Float32Array(), normals: new Float32Array(), layerIndices: new Float32Array(), indices: new Uint32Array() };
    switch (t) {
      case "_fill_opaque":
        e = this._fill_opaque, r = this._fillOpaqueArray, i.uvs = new Float32Array();
        break;
      case "_fill_pattern":
        e = this._fill_pattern, r = this._fillPatternArray, i.uvs = new Float32Array(), i.icons = new Float32Array();
        break;
      case "_fill_water":
        e = this._fill_water, r = this._fillWaterArray, i.uvs = new Float32Array();
        break;
      case "_fill_wood":
        e = this._fill_wood, r = this._fillWoodArray, i.uvs = new Float32Array();
        break;
      case "_fill_translucent":
        e = this._fill_translucent, r = this._fillTranslucentArray, i.uvs = new Float32Array();
        break;
      case "_fill_extrusion_opaque":
        e = this._fill_extrusion_opaque, r = this._fillExtrusionOpaqueArray, i.uvs = new Float32Array(), i.indices_side = new Uint32Array(), i.extentVertices = new Float32Array();
        break;
      case "_fill_extrusion_translucent":
        e = this._fill_extrusion_translucent, r = this._fillExtrusionTranslucentArray, i.uvs = new Float32Array(), i.indices_side = new Uint32Array(), i.extentVertices = new Float32Array();
        break;
      case "_line_opaque":
        e = this._line_opaque, r = this._lineOpaqueArray, i.widths = new Float32Array(), i.extentVertices = new Float32Array();
        break;
      case "_line_translucent":
        e = this._line_translucent, r = this._lineTranslucentArray, i.widths = new Float32Array(), i.extentVertices = new Float32Array();
        break;
      case "_line_dashed":
        e = this._line_dashed, r = this._lineDashedArray, i.widths = new Float32Array(), i.extentVertices = new Float32Array(), i.lengths = new Float32Array(), i.dashArrays = new Float32Array(), i.dashRatios = new Float32Array();
        break;
      default:
        return null;
    }
    if (!e || !r)
      return null;
    const s = Object.keys(i);
    for (let t4 = 0; t4 < s.length; t4++) {
      const r2 = s[t4];
      i[r2] = new i[r2].constructor(e[r2]);
    }
    let n = new Array(s.length).fill(0);
    for (let t4 = 0; t4 < r.length; t4++) {
      const e2 = r[t4];
      for (let t5 = 0; t5 < s.length; t5++) {
        const r2 = s[t5];
        e2[r2] && (this._flatArray(i[r2], e2[r2], n[t5]), n[t5] += e2[r2].length);
      }
    }
    return i;
  }
  _mergeArray(t, e) {
    const { vertices_opaque: r, colors_opaque: i, uvs_opaque: s, indices_opaque: n, indices_opaque_side: a, normals_opaque: o, widths_opaque: h, layerIndices_opaque: l, extentVertices_opaque: c, vertices_translucent: u, colors_translucent: d, uvs_translucent: p, indices_translucent: f, indices_translucent_side: _, normals_translucent: y, widths_translucent: m, layerIndices_translucent: g, extentVertices_translucent: x, vertices_dashed: M, colors_dashed: w, indices_dashed: b, normals_dashed: v, widths_dashed: S, layerIndices_dashed: C, extentVertices_dashed: E, lengths_dashed: P, dashArray_dashed: T, dashRatio_dashed: I, vertices_water: z, colors_water: A2, uvs_water: O, indices_water: N, normals_water: R, layerIndices_water: $, vertices_wood: V, colors_wood: G, uvs_wood: k, indices_wood: q, normals_wood: B, layerIndices_wood: F, vertices_pattern: L, colors_pattern: j, uvs_pattern: D, indices_pattern: U, normals_pattern: W, layerIndices_pattern: H, icons_pattern: X } = e;
    if (t instanceof FillExecuter)
      this._fillOpaqueArray.push({ vertices: r, colors: i, uvs: s, normals: o, layerIndices: l, indices: n }), this._fillTranslucentArray.push({ vertices: u, colors: d, uvs: p, normals: y, layerIndices: g, indices: f }), this._fillWaterArray.push({ vertices: z, colors: A2, uvs: O, normals: R, layerIndices: $, indices: N }), this._fillWoodArray.push({ vertices: V, colors: G, uvs: k, normals: B, layerIndices: F, indices: q }), this._fillPatternArray.push({ vertices: L, colors: j, uvs: D, normals: W, layerIndices: H, indices: U, icons: X }), this._fill_opaque.vertices += r.length, this._fill_opaque.uvs += s.length, this._fill_opaque.colors += i.length, this._fill_opaque.normals += o.length, this._fill_opaque.layerIndices += l.length, this._fill_opaque.indices += n.length, this._fill_translucent.vertices += u.length, this._fill_translucent.uvs += p.length, this._fill_translucent.colors += d.length, this._fill_translucent.normals += y.length, this._fill_translucent.layerIndices += g.length, this._fill_translucent.indices += f.length, this._fill_water.vertices += z.length, this._fill_water.uvs += O.length, this._fill_water.colors += A2.length, this._fill_water.normals += R.length, this._fill_water.layerIndices += $.length, this._fill_water.indices += N.length, this._fill_wood.vertices += V.length, this._fill_wood.uvs += k.length, this._fill_wood.colors += G.length, this._fill_wood.normals += B.length, this._fill_wood.layerIndices += F.length, this._fill_wood.indices += q.length, this._fill_pattern.vertices += L.length, this._fill_pattern.uvs += D.length, this._fill_pattern.colors += j.length, this._fill_pattern.normals += W.length, this._fill_pattern.layerIndices += H.length, this._fill_pattern.indices += U.length, this._fill_pattern.icons += X.length;
    else if (t instanceof FillExtrusionExecuter)
      this._fillExtrusionOpaqueArray.push({ vertices: r, colors: i, uvs: s, normals: o, layerIndices: l, indices: n, indices_side: a, extentVertices: c }), this._fillExtrusionTranslucentArray.push({ vertices: u, colors: d, uvs: p, normals: y, layerIndices: g, indices: f, indices_side: _, extentVertices: x }), this._fill_extrusion_opaque.vertices += r.length, this._fill_extrusion_opaque.colors += i.length, this._fill_extrusion_opaque.uvs += s.length, this._fill_extrusion_opaque.normals += o.length, this._fill_extrusion_opaque.layerIndices += l.length, this._fill_extrusion_opaque.indices += n.length, this._fill_extrusion_opaque.indices_side += a.length, this._fill_extrusion_opaque.extentVertices += c.length, this._fill_extrusion_translucent.vertices += u.length, this._fill_extrusion_translucent.colors += d.length, this._fill_extrusion_translucent.uvs += p.length, this._fill_extrusion_translucent.normals += y.length, this._fill_extrusion_translucent.layerIndices += g.length, this._fill_extrusion_translucent.indices += f.length, this._fill_extrusion_translucent.indices_side += _.length, this._fill_extrusion_translucent.extentVertices += x.length;
    else if (t instanceof LineExecuter)
      this._lineOpaqueArray.push({ vertices: r, colors: i, normals: o, indices: n, widths: h, layerIndices: l, extentVertices: c }), this._lineTranslucentArray.push({ vertices: u, colors: d, normals: y, indices: f, widths: m, layerIndices: g, extentVertices: x }), this._lineDashedArray.push({ vertices: M, colors: w, normals: v, indices: b, widths: S, layerIndices: C, lengths: P, extentVertices: E, dashArrays: T, dashRatios: I }), this._line_opaque.vertices += r.length, this._line_opaque.colors += i.length, this._line_opaque.normals += o.length, this._line_opaque.indices += n.length, this._line_opaque.widths += h.length, this._line_opaque.layerIndices += l.length, this._line_opaque.extentVertices += c.length, this._line_translucent.vertices += u.length, this._line_translucent.colors += d.length, this._line_translucent.normals += y.length, this._line_translucent.indices += f.length, this._line_translucent.widths += m.length, this._line_translucent.layerIndices += g.length, this._line_translucent.extentVertices += x.length, this._line_dashed.vertices += M.length, this._line_dashed.colors += w.length, this._line_dashed.normals += v.length, this._line_dashed.indices += b.length, this._line_dashed.widths += S.length, this._line_dashed.lengths += P.length, this._line_dashed.layerIndices += C.length, this._line_dashed.extentVertices += E.length, this._line_dashed.dashArrays += T.length, this._line_dashed.dashRatios += I.length;
    else if (t instanceof SymbolExecuter) {
      const { symbols: t4, iconVertices: r2, iconIndices: i2, iconUvs: s2, iconNormals: n2 } = e;
      this._symbol.symbols.push(...t4);
    }
  }
  _setupBackgroundLayer(t, e) {
    const r = new BackGroundExecuter(t, e, this._mapboxStyle);
    r.tileConfig = this._tileConfig, r.execute();
    const i = r.getExecutedData();
    this._background = i;
  }
  _getConstructor(t, e) {
    const r = e.type, i = this._displayOptions, s = i && false !== i.base, n = i && false !== i.link, a = i && false !== i.building, o = i && false !== i.poi;
    return 3 === t.type && "fill" === r && s ? FillExecuter : 3 === t.type && "fill-extrusion" === r && a ? FillExtrusionExecuter : 3 === t.type && "line" === r && n || 2 === t.type && "line" === r && n ? LineExecuter : 1 === t.type && "symbol" === r && o || 2 === t.type && "symbol" === r && o ? SymbolExecuter : (unsupportTypes.includes(r) && console.warn(`unsupport type: ${r}`), null);
  }
}
class SourceLayer {
  constructor(t = {}, e, r) {
    __publicField(this, "_layers", {});
    __publicField(this, "_fillOpaqueIndexOffset", 0);
    __publicField(this, "_fillTranslucentIndexOffset", 0);
    __publicField(this, "_fillExtrusionOpaqueIndexOffset", 0);
    __publicField(this, "_fillExtrusionTranslucentIndexOffset", 0);
    __publicField(this, "_fillPatternIndexOffset", 0);
    __publicField(this, "_lineOpaqueIndexOffset", 0);
    __publicField(this, "_lineTranslucentIndexOffset", 0);
    __publicField(this, "_lineDashedIndexOffset", 0);
    __publicField(this, "_fillWaterIndexOffset", 0);
    __publicField(this, "_fillWoodIndexOffset", 0);
    __publicField(this, "_iconIndexOffset", 0);
    __publicField(this, "tileKey", { x: 0, y: 0, z: 0 });
    __publicField(this, "tileSize", 256);
    this._layers = t, this.setDefaultIndexOffset(e), this.tileKey = r;
  }
  setDefaultIndexOffset(t) {
    this._fillOpaqueIndexOffset = t, this._fillTranslucentIndexOffset = t, this._fillExtrusionOpaqueIndexOffset = t, this._fillExtrusionTranslucentIndexOffset = t, this._lineOpaqueIndexOffset = t, this._lineTranslucentIndexOffset = t, this._lineDashedIndexOffset = t, this._fillWaterIndexOffset = t, this._fillWoodIndexOffset = t;
  }
  getTileLayer(t) {
    return this._layers[t];
  }
  setFillOpaqueIndexOffset(t) {
    this._fillOpaqueIndexOffset = t;
  }
  setFillTranslucentIndexOffset(t) {
    this._fillTranslucentIndexOffset = t;
  }
  setFillExtrusionOpaqueIndexOffset(t) {
    this._fillExtrusionOpaqueIndexOffset = t;
  }
  setFillExtrusionTranslucentIndexOffset(t) {
    this._fillExtrusionTranslucentIndexOffset = t;
  }
  setFillPatternIndexOffset(t) {
    this._fillPatternIndexOffset = t;
  }
  setLineOpaqueIndexOffset(t) {
    this._lineOpaqueIndexOffset = t;
  }
  setLineTranslucentIndexOffset(t) {
    this._lineTranslucentIndexOffset = t;
  }
  setLineDashedIndexOffset(t) {
    this._lineDashedIndexOffset = t;
  }
  getFillOpaqueIndexOffset() {
    return this._fillOpaqueIndexOffset;
  }
  getFillTranslucentIndexOffset() {
    return this._fillTranslucentIndexOffset;
  }
  getFillExtrusionOpaqueIndexOffset() {
    return this._fillExtrusionOpaqueIndexOffset;
  }
  getFillExtrusionTranslucentIndexOffset() {
    return this._fillExtrusionTranslucentIndexOffset;
  }
  getFillPatternIndexOffset() {
    return this._fillPatternIndexOffset;
  }
  getLineOpaqueIndexOffset() {
    return this._lineOpaqueIndexOffset;
  }
  getLineTranslucentIndexOffset() {
    return this._lineTranslucentIndexOffset;
  }
  getLineDashedIndexOffset() {
    return this._lineDashedIndexOffset;
  }
  setFillWaterIndexOffset(t) {
    this._fillWaterIndexOffset = t;
  }
  getFillWaterIndexOffset() {
    return this._fillWaterIndexOffset;
  }
  setFillWoodIndexOffset(t) {
    this._fillWoodIndexOffset = t;
  }
  getFillWoodIndexOffset() {
    return this._fillWoodIndexOffset;
  }
  setIconIndexOffset(t) {
    this._iconIndexOffset = t;
  }
  getIconIndexOffset() {
    return this._iconIndexOffset;
  }
  setLayers(t = {}) {
    this._layers = t;
  }
}
var unitbezier = UnitBezier;
function UnitBezier(t, e, r, i) {
  this.cx = 3 * t, this.bx = 3 * (r - t) - this.cx, this.ax = 1 - this.cx - this.bx, this.cy = 3 * e, this.by = 3 * (i - e) - this.cy, this.ay = 1 - this.cy - this.by, this.p1x = t, this.p1y = e, this.p2x = r, this.p2y = i;
}
UnitBezier.prototype = { sampleCurveX: function(t) {
  return ((this.ax * t + this.bx) * t + this.cx) * t;
}, sampleCurveY: function(t) {
  return ((this.ay * t + this.by) * t + this.cy) * t;
}, sampleCurveDerivativeX: function(t) {
  return (3 * this.ax * t + 2 * this.bx) * t + this.cx;
}, solveCurveX: function(t, e) {
  if (void 0 === e && (e = 1e-6), t < 0)
    return 0;
  if (t > 1)
    return 1;
  for (var r = t, i = 0; i < 8; i++) {
    var s = this.sampleCurveX(r) - t;
    if (Math.abs(s) < e)
      return r;
    var n = this.sampleCurveDerivativeX(r);
    if (Math.abs(n) < 1e-6)
      break;
    r -= s / n;
  }
  var a = 0, o = 1;
  for (r = t, i = 0; i < 20 && (s = this.sampleCurveX(r), !(Math.abs(s - t) < e)); i++)
    t > s ? a = r : o = r, r = 0.5 * (o - a) + a;
  return r;
}, solve: function(t, e) {
  return this.sampleCurveY(this.solveCurveX(t, e));
} };
const NullType = { kind: "null" }, NumberType = { kind: "number" }, StringType = { kind: "string" }, BooleanType = { kind: "boolean" }, ColorType = { kind: "color" }, ObjectType = { kind: "object" }, ValueType = { kind: "value" }, ErrorType = { kind: "error" }, CollatorType = { kind: "collator" }, FormattedType = { kind: "formatted" }, ResolvedImageType = { kind: "resolvedImage" };
function array(t, e) {
  return { kind: "array", itemType: t, N: e };
}
function toString$1(t) {
  if ("array" === t.kind) {
    const e = toString$1(t.itemType);
    return "number" == typeof t.N ? `array<${e}, ${t.N}>` : "value" === t.itemType.kind ? "array" : `array<${e}>`;
  }
  return t.kind;
}
const valueMemberTypes = [NullType, NumberType, StringType, BooleanType, ColorType, FormattedType, ObjectType, array(ValueType), ResolvedImageType];
function checkSubtype(t, e) {
  if ("error" === e.kind)
    return null;
  if ("array" === t.kind) {
    if ("array" === e.kind && (0 === e.N && "value" === e.itemType.kind || !checkSubtype(t.itemType, e.itemType)) && ("number" != typeof t.N || t.N === e.N))
      return null;
  } else {
    if (t.kind === e.kind)
      return null;
    if ("value" === t.kind) {
      for (const t4 of valueMemberTypes)
        if (!checkSubtype(t4, e))
          return null;
    }
  }
  return `Expected ${toString$1(t)} but found ${toString$1(e)} instead.`;
}
function isValidType(t, e) {
  return e.some((e2) => e2.kind === t.kind);
}
function isValidNativeType(t, e) {
  return e.some((e2) => "null" === e2 ? null === t : "array" === e2 ? Array.isArray(t) : "object" === e2 ? t && !Array.isArray(t) && "object" == typeof t : e2 === typeof t);
}
function findStopLessThanOrEqualTo(t, e) {
  const r = t.length - 1;
  let i, s, n = 0, a = r, o = 0;
  for (; n <= a; )
    if (o = Math.floor((n + a) / 2), i = t[o], s = t[o + 1], i <= e) {
      if (o === r || e < s)
        return o;
      n = o + 1;
    } else {
      if (!(i > e))
        throw new Error("Input is not a number.");
      a = o - 1;
    }
  return 0;
}
class Interpolate {
  constructor(t, e, r, i, s) {
    __publicField(this, "type");
    __publicField(this, "operator");
    __publicField(this, "interpolation");
    __publicField(this, "input");
    __publicField(this, "labels");
    __publicField(this, "outputs");
    this.type = t, this.operator = e, this.interpolation = r, this.input = i, this.labels = [], this.outputs = [];
    for (const [t4, e2] of s)
      this.labels.push(t4), this.outputs.push(e2);
  }
  static interpolationFactor(t, e, r, i) {
    let s = 0;
    if ("exponential" === t.name)
      s = exponentialInterpolation(e, t.base, r, i);
    else if ("linear" === t.name)
      s = exponentialInterpolation(e, 1, r, i);
    else if ("cubic-bezier" === t.name) {
      const n = t.controlPoints;
      s = new unitbezier(n[0], n[1], n[2], n[3]).solve(exponentialInterpolation(e, 1, r, i));
    }
    return s;
  }
  static parse(t, e) {
    let [r, i, s, ...n] = t;
    if (!Array.isArray(i) || 0 === i.length)
      return e.error("Expected an interpolation type expression.", 1);
    if ("linear" === i[0])
      i = { name: "linear" };
    else if ("exponential" === i[0]) {
      const t4 = i[1];
      if ("number" != typeof t4)
        return e.error("Exponential interpolation requires a numeric base.", 1, 1);
      i = { name: "exponential", base: t4 };
    } else {
      if ("cubic-bezier" !== i[0])
        return e.error(`Unknown interpolation type ${String(i[0])}`, 1, 0);
      {
        const t4 = i.slice(1);
        if (4 !== t4.length || t4.some((t5) => "number" != typeof t5 || t5 < 0 || t5 > 1))
          return e.error("Cubic bezier interpolation requires four numeric arguments with values between 0 and 1.", 1);
        i = { name: "cubic-bezier", controlPoints: t4 };
      }
    }
    if (t.length - 1 < 4)
      return e.error(`Expected at least 4 arguments, but found only ${t.length - 1}.`);
    if ((t.length - 1) % 2 != 0)
      return e.error("Expected an even number of arguments.");
    if (s = e.parse(s, 2, NumberType), !s)
      return null;
    const a = [];
    let o = null;
    "interpolate-hcl" === r || "interpolate-lab" === r ? o = ColorType : e.expectedType && "value" !== e.expectedType.kind && (o = e.expectedType);
    for (let t4 = 0; t4 < n.length; t4 += 2) {
      const r2 = n[t4], i2 = n[t4 + 1], s2 = t4 + 3, h = t4 + 4;
      if ("number" != typeof r2)
        return e.error('Input/output pairs for "interpolate" expressions must be defined using literal numeric values (not computed expressions) for the input values.', s2);
      if (a.length && a[a.length - 1][0] >= r2)
        return e.error('Input/output pairs for "interpolate" expressions must be arranged with input values in strictly ascending order.', s2);
      const l = e.parse(i2, h, o);
      if (!l)
        return null;
      o = o || l.type, a.push([r2, l]);
    }
    return "number" === o.kind || "color" === o.kind || "array" === o.kind && "number" === o.itemType.kind && "number" == typeof o.N ? new Interpolate(o, r, i, s, a) : e.error(`Type ${toString$1(o)} is not interpolatable.`);
  }
  evaluate(t) {
    const e = this.labels, r = this.outputs;
    if (1 === e.length)
      return r[0].evaluate(t);
    const i = this.input.evaluate(t);
    if (i <= e[0])
      return r[0].evaluate(t);
    const s = e.length;
    if (i >= e[s - 1])
      return r[s - 1].evaluate(t);
    const n = findStopLessThanOrEqualTo(e, i), a = e[n], o = e[n + 1], h = Interpolate.interpolationFactor(this.interpolation, i, a, o), l = r[n].evaluate(t), c = r[n + 1].evaluate(t);
    return "interpolate" === this.operator ? interpolate[this.type.kind.toLowerCase()](l, c, h) : "interpolate-hcl" === this.operator ? hcl.reverse(hcl.interpolate(hcl.forward(l), hcl.forward(c), h)) : lab.reverse(lab.interpolate(lab.forward(l), lab.forward(c), h));
  }
  eachChild(t) {
    t(this.input);
    for (const e of this.outputs)
      t(e);
  }
  outputDefined() {
    return this.outputs.every((t) => t.outputDefined());
  }
  serialize() {
    let t;
    t = "linear" === this.interpolation.name ? ["linear"] : "exponential" === this.interpolation.name ? 1 === this.interpolation.base ? ["linear"] : ["exponential", this.interpolation.base] : ["cubic-bezier"].concat(this.interpolation.controlPoints);
    const e = [this.operator, t, this.input.serialize()];
    for (let t4 = 0; t4 < this.labels.length; t4++)
      e.push(this.labels[t4], this.outputs[t4].serialize());
    return e;
  }
}
function exponentialInterpolation(t, e, r, i) {
  const s = i - r, n = t - r;
  return 0 === s ? 0 : 1 === e ? n / s : (Math.pow(e, n) - 1) / (Math.pow(e, s) - 1);
}
const geometryTypes = ["Unknown", "Point", "LineString", "Polygon"];
class EvaluationContext {
  constructor() {
    __publicField(this, "globals");
    __publicField(this, "feature");
    __publicField(this, "featureState");
    __publicField(this, "formattedSection");
    __publicField(this, "availableImages");
    __publicField(this, "canonical");
    __publicField(this, "featureTileCoord");
    __publicField(this, "featureDistanceData");
    __publicField(this, "_parseColorCache");
    this.globals = null, this.feature = null, this.featureState = null, this.formattedSection = null, this._parseColorCache = {}, this.availableImages = null, this.canonical = null, this.featureTileCoord = null, this.featureDistanceData = null;
  }
  id() {
    return this.feature && void 0 !== this.feature.id ? this.feature.id : null;
  }
  geometryType() {
    return this.feature ? "number" == typeof this.feature.type ? geometryTypes[this.feature.type] : this.feature.type : null;
  }
  geometry() {
    return this.feature && "geometry" in this.feature ? this.feature.geometry : null;
  }
  canonicalID() {
    return this.canonical;
  }
  properties() {
    return this.feature && this.feature.properties || {};
  }
  distanceFromCenter() {
    if (this.featureTileCoord && this.featureDistanceData) {
      const t = this.featureDistanceData.center, e = this.featureDistanceData.scale, { x: r, y: i } = this.featureTileCoord, s = r * e - t[0], n = i * e - t[1];
      return this.featureDistanceData.bearing[0] * s + this.featureDistanceData.bearing[1] * n;
    }
    return 0;
  }
  parseColor(t) {
    let e = this._parseColorCache[t];
    return e || (e = this._parseColorCache[t] = Color.parse(t)), e;
  }
}
class ParsingError extends Error {
  constructor(t, e) {
    super(e);
    __publicField(this, "key");
    __publicField(this, "message");
    this.message = e, this.key = t;
  }
}
class Collator {
  constructor(t, e, r) {
    __publicField(this, "locale");
    __publicField(this, "sensitivity");
    __publicField(this, "collator");
    this.sensitivity = t ? e ? "variant" : "case" : e ? "accent" : "base", this.locale = r, this.collator = new Intl.Collator(this.locale ? this.locale : [], { sensitivity: this.sensitivity, usage: "search" });
  }
  compare(t, e) {
    return this.collator.compare(t, e);
  }
  resolvedLocale() {
    return new Intl.Collator(this.locale ? this.locale : []).resolvedOptions().locale;
  }
}
class ResolvedImage {
  constructor(t) {
    __publicField(this, "name");
    __publicField(this, "available");
    this.name = t.name, this.available = t.available;
  }
  toString() {
    return this.name;
  }
  static fromString(t) {
    return t ? new ResolvedImage({ name: t, available: false }) : null;
  }
  serialize() {
    return ["image", this.name];
  }
}
function validateRGBA(t, e, r, i) {
  if (!("number" == typeof t && t >= 0 && t <= 255 && "number" == typeof e && e >= 0 && e <= 255 && "number" == typeof r && r >= 0 && r <= 255)) {
    return `Invalid rgba value [${("number" == typeof i ? [t, e, r, i] : [t, e, r]).join(", ")}]: 'r', 'g', and 'b' must be between 0 and 255.`;
  }
  return void 0 === i || "number" == typeof i && i >= 0 && i <= 1 ? null : `Invalid rgba value [${[t, e, r, i].join(", ")}]: 'a' must be between 0 and 1.`;
}
function isValue(t) {
  if (null === t)
    return true;
  if ("string" == typeof t)
    return true;
  if ("boolean" == typeof t)
    return true;
  if ("number" == typeof t)
    return true;
  if (t instanceof Color)
    return true;
  if (t instanceof Collator)
    return true;
  if (t instanceof Formatted)
    return true;
  if (t instanceof ResolvedImage)
    return true;
  if (Array.isArray(t)) {
    for (const e of t)
      if (!isValue(e))
        return false;
    return true;
  }
  if ("object" == typeof t) {
    for (const e in t)
      if (!isValue(t[e]))
        return false;
    return true;
  }
  return false;
}
function typeOf(t) {
  if (null === t)
    return NullType;
  if ("string" == typeof t)
    return StringType;
  if ("boolean" == typeof t)
    return BooleanType;
  if ("number" == typeof t)
    return NumberType;
  if (t instanceof Color)
    return ColorType;
  if (t instanceof Collator)
    return CollatorType;
  if (t instanceof Formatted)
    return FormattedType;
  if (t instanceof ResolvedImage)
    return ResolvedImageType;
  if (Array.isArray(t)) {
    const e = t.length;
    let r;
    for (const e2 of t) {
      const t4 = typeOf(e2);
      if (r) {
        if (r === t4)
          continue;
        r = ValueType;
        break;
      }
      r = t4;
    }
    return array(r || ValueType, e);
  }
  return assert("object" == typeof t), ObjectType;
}
function toString(t) {
  const e = typeof t;
  return null === t ? "" : "string" === e || "number" === e || "boolean" === e ? String(t) : t instanceof Color || t instanceof Formatted || t instanceof ResolvedImage ? t.toString() : JSON.stringify(t);
}
class Literal {
  constructor(t, e) {
    __publicField(this, "type");
    __publicField(this, "value");
    this.type = t, this.value = e;
  }
  static parse(t, e) {
    if (2 !== t.length)
      return e.error(`'literal' expression requires exactly one argument,
             but found ${t.length - 1} instead.`);
    if (!isValue(t[1]))
      return e.error("invalid value");
    const r = t[1];
    let i = typeOf(r);
    const s = e.expectedType;
    return "array" !== i.kind || 0 !== i.N || !s || "array" !== s.kind || "number" == typeof s.N && 0 !== s.N || (i = s), new Literal(i, r);
  }
  evaluate() {
    return this.value;
  }
  eachChild() {
  }
  outputDefined() {
    return true;
  }
  serialize() {
    return "array" === this.type.kind || "object" === this.type.kind ? ["literal", this.value] : this.value instanceof Color ? ["rgba"].concat(this.value.toArray()) : this.value instanceof Formatted ? this.value.serialize() : (assert(null === this.value || "string" == typeof this.value || "number" == typeof this.value || "boolean" == typeof this.value), this.value);
  }
}
const types$1 = { string: StringType, number: NumberType, boolean: BooleanType, object: ObjectType };
class Assertion {
  constructor(t, e) {
    __publicField(this, "type");
    __publicField(this, "args");
    this.type = t, this.args = e;
  }
  static parse(t, e) {
    if (t.length < 2)
      return e.error("Expected at least one argument.");
    let r, i = 1;
    const s = t[0];
    if ("array" === s) {
      let s2, n2;
      if (t.length > 2) {
        const r2 = t[1];
        if ("string" != typeof r2 || !(r2 in types$1) || "object" === r2)
          return e.error('The item type argument of "array" must be one of string, number, boolean', 1);
        s2 = types$1[r2], i++;
      } else
        s2 = ValueType;
      if (t.length > 3) {
        if (null !== t[2] && ("number" != typeof t[2] || t[2] < 0 || t[2] !== Math.floor(t[2])))
          return e.error('The length argument to "array" must be a positive integer literal', 2);
        n2 = t[2], i++;
      }
      r = array(s2, n2);
    } else
      assert(types$1[s]), r = types$1[s];
    const n = [];
    for (; i < t.length; i++) {
      const r2 = e.parse(t[i], i, ValueType);
      if (!r2)
        return null;
      n.push(r2);
    }
    return new Assertion(r, n);
  }
  evaluate(t) {
    for (let e = 0; e < this.args.length; e++) {
      const r = this.args[e].evaluate(t);
      if (!checkSubtype(this.type, typeOf(r)))
        return r;
      if (e === this.args.length - 1)
        throw new Error(`Expected value to be of type ${toString$1(this.type)}, but found ${toString$1(typeOf(r))} instead.`);
    }
    return assert(false), null;
  }
  eachChild(t) {
    this.args.forEach(t);
  }
  outputDefined() {
    return this.args.every((t) => t.outputDefined());
  }
  serialize() {
    const t = this.type, e = [t.kind];
    if ("array" === t.kind) {
      const r = t.itemType;
      if ("string" === r.kind || "number" === r.kind || "boolean" === r.kind) {
        e.push(r.kind);
        const i = t.N;
        ("number" == typeof i || this.args.length > 1) && e.push(i);
      }
    }
    return e.concat(this.args.map((t4) => t4.serialize()));
  }
}
class FormatExpression {
  constructor(t) {
    __publicField(this, "type");
    __publicField(this, "sections");
    this.type = FormattedType, this.sections = t;
  }
  static parse(t, e) {
    if (t.length < 2)
      return e.error("Expected at least one argument.");
    const r = t[1];
    if (!Array.isArray(r) && "object" == typeof r)
      return e.error("First argument must be an image or text section.");
    const i = [];
    let s = false;
    for (let r2 = 1; r2 <= t.length - 1; ++r2) {
      const n = t[r2];
      if (s && "object" == typeof n && !Array.isArray(n)) {
        s = false;
        let t4 = null;
        if (n["font-scale"] && (t4 = e.parse(n["font-scale"], 1, NumberType), !t4))
          return null;
        let r3 = null;
        if (n["text-font"] && (r3 = e.parse(n["text-font"], 1, array(StringType)), !r3))
          return null;
        let a = null;
        if (n["text-color"] && (a = e.parse(n["text-color"], 1, ColorType), !a))
          return null;
        const o = i[i.length - 1];
        o.scale = t4, o.font = r3, o.textColor = a;
      } else {
        const n2 = e.parse(t[r2], 1, ValueType);
        if (!n2)
          return null;
        const a = n2.type.kind;
        if ("string" !== a && "value" !== a && "null" !== a && "resolvedImage" !== a)
          return e.error("Formatted text type must be 'string', 'value', 'image' or 'null'.");
        s = true, i.push({ content: n2, scale: null, font: null, textColor: null });
      }
    }
    return new FormatExpression(i);
  }
  evaluate(t) {
    return new Formatted(this.sections.map((e) => {
      const r = e.content.evaluate(t);
      return typeOf(r) === ResolvedImageType ? new FormattedSection("", r, null, null, null) : new FormattedSection(toString(r), null, e.scale ? e.scale.evaluate(t) : null, e.font ? e.font.evaluate(t).join(",") : null, e.textColor ? e.textColor.evaluate(t) : null);
    }));
  }
  eachChild(t) {
    for (const e of this.sections)
      t(e.content), e.scale && t(e.scale), e.font && t(e.font), e.textColor && t(e.textColor);
  }
  outputDefined() {
    return false;
  }
  serialize() {
    const t = ["format"];
    for (const e of this.sections) {
      t.push(e.content.serialize());
      const r = {};
      e.scale && (r["font-scale"] = e.scale.serialize()), e.font && (r["text-font"] = e.font.serialize()), e.textColor && (r["text-color"] = e.textColor.serialize()), t.push(r);
    }
    return t;
  }
}
class ImageExpression {
  constructor(t) {
    __publicField(this, "type");
    __publicField(this, "input");
    this.type = ResolvedImageType, this.input = t;
  }
  static parse(t, e) {
    if (2 !== t.length)
      return e.error("Expected two arguments.");
    const r = e.parse(t[1], 1, StringType);
    return r ? new ImageExpression(r) : e.error("No image name provided.");
  }
  evaluate(t) {
    const e = this.input.evaluate(t), r = ResolvedImage.fromString(e);
    return r && t.availableImages && (r.available = t.availableImages.indexOf(e) > -1), r;
  }
  eachChild(t) {
    t(this.input);
  }
  outputDefined() {
    return false;
  }
  serialize() {
    return ["image", this.input.serialize()];
  }
}
const types = { "to-boolean": BooleanType, "to-color": ColorType, "to-number": NumberType, "to-string": StringType };
class Coercion {
  constructor(t, e) {
    __publicField(this, "type");
    __publicField(this, "args");
    this.type = t, this.args = e;
  }
  static parse(t, e) {
    if (t.length < 2)
      return e.error("Expected at least one argument.");
    const r = t[0];
    if (assert(types[r]), ("to-boolean" === r || "to-string" === r) && 2 !== t.length)
      return e.error("Expected one argument.");
    const i = types[r], s = [];
    for (let r2 = 1; r2 < t.length; r2++) {
      const i2 = e.parse(t[r2], r2, ValueType);
      if (!i2)
        return null;
      s.push(i2);
    }
    return new Coercion(i, s);
  }
  evaluate(t) {
    if ("boolean" === this.type.kind)
      return Boolean(this.args[0].evaluate(t));
    if ("color" === this.type.kind) {
      let e, r;
      for (const i of this.args) {
        if (e = i.evaluate(t), r = null, e instanceof Color)
          return e;
        if ("string" == typeof e) {
          const r2 = t.parseColor(e);
          if (r2)
            return r2;
        } else if (Array.isArray(e) && (r = e.length < 3 || e.length > 4 ? `Invalid rbga value ${JSON.stringify(e)}: expected an array containing either three or four numeric values.` : validateRGBA(e[0], e[1], e[2], e[3]), !r))
          return new Color(e[0] / 255, e[1] / 255, e[2] / 255, e[3]);
      }
      throw new Error(r || `Could not parse color from value '${"string" == typeof e ? e : String(JSON.stringify(e))}'`);
    }
    if ("number" === this.type.kind) {
      let e = null;
      for (const r of this.args) {
        if (e = r.evaluate(t), null === e)
          return 0;
        const i = Number(e);
        if (!isNaN(i))
          return i;
      }
      throw new Error(`Could not convert ${JSON.stringify(e)} to number.`);
    }
    return "formatted" === this.type.kind ? Formatted.fromString(toString(this.args[0].evaluate(t))) : "resolvedImage" === this.type.kind ? ResolvedImage.fromString(toString(this.args[0].evaluate(t))) : toString(this.args[0].evaluate(t));
  }
  eachChild(t) {
    this.args.forEach(t);
  }
  outputDefined() {
    return this.args.every((t) => t.outputDefined());
  }
  serialize() {
    if ("formatted" === this.type.kind)
      return new FormatExpression([{ content: this.args[0], scale: null, font: null, textColor: null }]).serialize();
    if ("resolvedImage" === this.type.kind)
      return new ImageExpression(this.args[0]).serialize();
    const t = [`to-${this.type.kind}`];
    return this.eachChild((e) => {
      t.push(e.serialize());
    }), t;
  }
}
class CollatorExpression {
  constructor(t, e, r) {
    __publicField(this, "type");
    __publicField(this, "caseSensitive");
    __publicField(this, "diacriticSensitive");
    __publicField(this, "locale");
    this.type = CollatorType, this.locale = r, this.caseSensitive = t, this.diacriticSensitive = e;
  }
  static parse(t, e) {
    if (2 !== t.length)
      return e.error("Expected one argument.");
    const r = t[1];
    if ("object" != typeof r || Array.isArray(r))
      return e.error("Collator options argument must be an object.");
    const i = e.parse(void 0 !== r["case-sensitive"] && r["case-sensitive"], 1, BooleanType);
    if (!i)
      return null;
    const s = e.parse(void 0 !== r["diacritic-sensitive"] && r["diacritic-sensitive"], 1, BooleanType);
    if (!s)
      return null;
    let n = null;
    return r.locale && (n = e.parse(r.locale, 1, StringType), !n) ? null : new CollatorExpression(i, s, n);
  }
  evaluate(t) {
    return new Collator(this.caseSensitive.evaluate(t), this.diacriticSensitive.evaluate(t), this.locale ? this.locale.evaluate(t) : null);
  }
  eachChild(t) {
    t(this.caseSensitive), t(this.diacriticSensitive), this.locale && t(this.locale);
  }
  outputDefined() {
    return false;
  }
  serialize() {
    const t = {};
    return t["case-sensitive"] = this.caseSensitive.serialize(), t["diacritic-sensitive"] = this.diacriticSensitive.serialize(), this.locale && (t.locale = this.locale.serialize()), ["collator", t];
  }
}
const EXTENT = 8192;
function updateBBox(t, e) {
  t[0] = Math.min(t[0], e[0]), t[1] = Math.min(t[1], e[1]), t[2] = Math.max(t[2], e[0]), t[3] = Math.max(t[3], e[1]);
}
function mercatorXfromLng(t) {
  return (180 + t) / 360;
}
function mercatorYfromLat(t) {
  return (180 - 180 / Math.PI * Math.log(Math.tan(Math.PI / 4 + t * Math.PI / 360))) / 360;
}
function boxWithinBox(t, e) {
  return !(t[0] <= e[0]) && (!(t[2] >= e[2]) && (!(t[1] <= e[1]) && !(t[3] >= e[3])));
}
function getTileCoordinates(t, e) {
  const r = mercatorXfromLng(t[0]), i = mercatorYfromLat(t[1]), s = Math.pow(2, e.z);
  return [Math.round(r * s * EXTENT), Math.round(i * s * EXTENT)];
}
function onBoundary(t, e, r) {
  const i = t[0] - e[0], s = t[1] - e[1], n = t[0] - r[0], a = t[1] - r[1];
  return i * a - n * s == 0 && i * n <= 0 && s * a <= 0;
}
function rayIntersect(t, e, r) {
  return e[1] > t[1] != r[1] > t[1] && t[0] < (r[0] - e[0]) * (t[1] - e[1]) / (r[1] - e[1]) + e[0];
}
function pointWithinPolygon(t, e) {
  let r = false;
  for (let i = 0, s = e.length; i < s; i++) {
    const s2 = e[i];
    for (let e2 = 0, i2 = s2.length; e2 < i2 - 1; e2++) {
      if (onBoundary(t, s2[e2], s2[e2 + 1]))
        return false;
      rayIntersect(t, s2[e2], s2[e2 + 1]) && (r = !r);
    }
  }
  return r;
}
function pointWithinPolygons(t, e) {
  for (let r = 0; r < e.length; r++)
    if (pointWithinPolygon(t, e[r]))
      return true;
  return false;
}
function perp(t, e) {
  return t[0] * e[1] - t[1] * e[0];
}
function twoSided(t, e, r, i) {
  const s = t[0] - r[0], n = t[1] - r[1], a = e[0] - r[0], o = e[1] - r[1], h = i[0] - r[0], l = i[1] - r[1], c = s * l - h * n, u = a * l - h * o;
  return c > 0 && u < 0 || c < 0 && u > 0;
}
function lineIntersectLine(t, e, r, i) {
  const s = [e[0] - t[0], e[1] - t[1]];
  return 0 !== perp([i[0] - r[0], i[1] - r[1]], s) && !(!twoSided(t, e, r, i) || !twoSided(r, i, t, e));
}
function lineIntersectPolygon(t, e, r) {
  for (const i of r)
    for (let r2 = 0; r2 < i.length - 1; ++r2)
      if (lineIntersectLine(t, e, i[r2], i[r2 + 1]))
        return true;
  return false;
}
function lineStringWithinPolygon(t, e) {
  for (let r = 0; r < t.length; ++r)
    if (!pointWithinPolygon(t[r], e))
      return false;
  for (let r = 0; r < t.length - 1; ++r)
    if (lineIntersectPolygon(t[r], t[r + 1], e))
      return false;
  return true;
}
function lineStringWithinPolygons(t, e) {
  for (let r = 0; r < e.length; r++)
    if (lineStringWithinPolygon(t, e[r]))
      return true;
  return false;
}
function getTilePolygon(t, e, r) {
  const i = [];
  for (let s = 0; s < t.length; s++) {
    const n = [];
    for (let i2 = 0; i2 < t[s].length; i2++) {
      const a = getTileCoordinates(t[s][i2], r);
      updateBBox(e, a), n.push(a);
    }
    i.push(n);
  }
  return i;
}
function getTilePolygons(t, e, r) {
  const i = [];
  for (let s = 0; s < t.length; s++) {
    const n = getTilePolygon(t[s], e, r);
    i.push(n);
  }
  return i;
}
function updatePoint(t, e, r, i) {
  if (t[0] < r[0] || t[0] > r[2]) {
    const e2 = 0.5 * i;
    let s = t[0] - r[0] > e2 ? -i : r[0] - t[0] > e2 ? i : 0;
    0 === s && (s = t[0] - r[2] > e2 ? -i : r[2] - t[0] > e2 ? i : 0), t[0] += s;
  }
  updateBBox(e, t);
}
function resetBBox(t) {
  t[0] = t[1] = 1 / 0, t[2] = t[3] = -1 / 0;
}
function getTilePoints(t, e, r, i) {
  const s = Math.pow(2, i.z) * EXTENT, n = [i.x * EXTENT, i.y * EXTENT], a = [];
  if (!t)
    return a;
  for (const i2 of t)
    for (const t4 of i2) {
      const i3 = [t4.x + n[0], t4.y + n[1]];
      updatePoint(i3, e, r, s), a.push(i3);
    }
  return a;
}
function getTileLines(t, e, r, i) {
  const s = Math.pow(2, i.z) * EXTENT, n = [i.x * EXTENT, i.y * EXTENT], a = [];
  if (!t)
    return a;
  for (const r2 of t) {
    const t4 = [];
    for (const i2 of r2) {
      const r3 = [i2.x + n[0], i2.y + n[1]];
      updateBBox(e, r3), t4.push(r3);
    }
    a.push(t4);
  }
  if (e[2] - e[0] <= s / 2) {
    resetBBox(e);
    for (const t4 of a)
      for (const i2 of t4)
        updatePoint(i2, e, r, s);
  }
  return a;
}
function pointsWithinPolygons(t, e) {
  const r = [1 / 0, 1 / 0, -1 / 0, -1 / 0], i = [1 / 0, 1 / 0, -1 / 0, -1 / 0], s = t.canonicalID();
  if (!s)
    return false;
  if ("Polygon" === e.type) {
    const n = getTilePolygon(e.coordinates, i, s), a = getTilePoints(t.geometry(), r, i, s);
    if (!boxWithinBox(r, i))
      return false;
    for (const t4 of a)
      if (!pointWithinPolygon(t4, n))
        return false;
  }
  if ("MultiPolygon" === e.type) {
    const n = getTilePolygons(e.coordinates, i, s), a = getTilePoints(t.geometry(), r, i, s);
    if (!boxWithinBox(r, i))
      return false;
    for (const t4 of a)
      if (!pointWithinPolygons(t4, n))
        return false;
  }
  return true;
}
function linesWithinPolygons(t, e) {
  const r = [1 / 0, 1 / 0, -1 / 0, -1 / 0], i = [1 / 0, 1 / 0, -1 / 0, -1 / 0], s = t.canonicalID();
  if (!s)
    return false;
  if ("Polygon" === e.type) {
    const n = getTilePolygon(e.coordinates, i, s), a = getTileLines(t.geometry(), r, i, s);
    if (!boxWithinBox(r, i))
      return false;
    for (const t4 of a)
      if (!lineStringWithinPolygon(t4, n))
        return false;
  }
  if ("MultiPolygon" === e.type) {
    const n = getTilePolygons(e.coordinates, i, s), a = getTileLines(t.geometry(), r, i, s);
    if (!boxWithinBox(r, i))
      return false;
    for (const t4 of a)
      if (!lineStringWithinPolygons(t4, n))
        return false;
  }
  return true;
}
class Within {
  constructor(t, e) {
    __publicField(this, "type");
    __publicField(this, "geojson");
    __publicField(this, "geometries");
    this.type = BooleanType, this.geojson = t, this.geometries = e;
  }
  static parse(t, e) {
    if (2 !== t.length)
      return e.error(`'within' expression requires exactly one argument, but found ${t.length - 1} instead.`);
    if (isValue(t[1])) {
      const e2 = t[1];
      if ("FeatureCollection" === e2.type)
        for (let t4 = 0; t4 < e2.features.length; ++t4) {
          const r = e2.features[t4].geometry.type;
          if ("Polygon" === r || "MultiPolygon" === r)
            return new Within(e2, e2.features[t4].geometry);
        }
      else if ("Feature" === e2.type) {
        const t4 = e2.geometry.type;
        if ("Polygon" === t4 || "MultiPolygon" === t4)
          return new Within(e2, e2.geometry);
      } else if ("Polygon" === e2.type || "MultiPolygon" === e2.type)
        return new Within(e2, e2);
    }
    return e.error("'within' expression requires valid geojson object that contains polygon geometry type.");
  }
  evaluate(t) {
    if (null != t.geometry() && null != t.canonicalID()) {
      if ("Point" === t.geometryType())
        return pointsWithinPolygons(t, this.geometries);
      if ("LineString" === t.geometryType())
        return linesWithinPolygons(t, this.geometries);
    }
    return false;
  }
  eachChild() {
  }
  outputDefined() {
    return true;
  }
  serialize() {
    return ["within", this.geojson];
  }
}
class Var {
  constructor(t, e) {
    __publicField(this, "type");
    __publicField(this, "name");
    __publicField(this, "boundExpression");
    this.type = e.type, this.name = t, this.boundExpression = e;
  }
  static parse(t, e) {
    if (2 !== t.length || "string" != typeof t[1])
      return e.error("'var' expression requires exactly one string literal argument.");
    const r = t[1];
    return e.scope.has(r) ? new Var(r, e.scope.get(r)) : e.error(`Unknown variable "${r}". Make sure "${r}" has been bound in an enclosing
                "let" expression before using it.`, 1);
  }
  evaluate(t) {
    return this.boundExpression.evaluate(t);
  }
  eachChild() {
  }
  outputDefined() {
    return false;
  }
  serialize() {
    return ["var", this.name];
  }
}
class Scope {
  constructor(t, e = []) {
    __publicField(this, "parent");
    __publicField(this, "bindings");
    this.parent = t, this.bindings = {};
    for (const [t4, r] of e)
      this.bindings[t4] = r;
  }
  concat(t) {
    return new Scope(this, t);
  }
  get(t) {
    if (this.bindings[t])
      return this.bindings[t];
    if (this.parent)
      return this.parent.get(t);
    throw new Error(`${t} not found in scope.`);
  }
  has(t) {
    return !!this.bindings[t] || !!this.parent && this.parent.has(t);
  }
}
class ParsingContext {
  constructor(t, e = [], r, i = new Scope(), s = []) {
    __publicField(this, "registry");
    __publicField(this, "path");
    __publicField(this, "key");
    __publicField(this, "scope");
    __publicField(this, "errors");
    __publicField(this, "expectedType");
    this.registry = t, this.path = e, this.key = e.map((t4) => `[${t4}]`).join(""), this.scope = i, this.errors = s, this.expectedType = r;
  }
  parse(t, e, r, i, s = {}) {
    return e ? this.concat(e, r, i)._parse(t, s) : this._parse(t, s);
  }
  _parse(t, e) {
    function r(t4, e2, r2) {
      return "assert" === r2 ? new Assertion(e2, [t4]) : "coerce" === r2 ? new Coercion(e2, [t4]) : t4;
    }
    if (null !== t && "string" != typeof t && "boolean" != typeof t && "number" != typeof t || (t = ["literal", t]), Array.isArray(t)) {
      if (0 === t.length)
        return this.error('Expected an array with at least one element. If you wanted a literal array, use ["literal", []].');
      const i = t[0];
      if ("string" != typeof i)
        return this.error(`Expression name must be a string, but found ${typeof i} instead.
                    If you wanted a literal array, use ["literal", [...]].`, 0), null;
      const s = this.registry[i];
      if (s) {
        let i2 = s.parse(t, this);
        if (!i2)
          return null;
        if (this.expectedType) {
          const t4 = this.expectedType, s2 = i2.type;
          if ("string" !== t4.kind && "number" !== t4.kind && "boolean" !== t4.kind && "object" !== t4.kind && "array" !== t4.kind || "value" !== s2.kind)
            if ("color" !== t4.kind && "formatted" !== t4.kind && "resolvedImage" !== t4.kind || "value" !== s2.kind && "string" !== s2.kind) {
              if (this.checkSubtype(t4, s2))
                return null;
            } else
              i2 = r(i2, t4, e.typeAnnotation || "coerce");
          else
            i2 = r(i2, t4, e.typeAnnotation || "assert");
        }
        if (!(i2 instanceof Literal) && "resolvedImage" !== i2.type.kind && isConstant(i2)) {
          const t4 = new EvaluationContext();
          try {
            i2 = new Literal(i2.type, i2.evaluate(t4));
          } catch (t5) {
            return this.error(t5.message), null;
          }
        }
        return i2;
      }
      return this.error(`Unknown expression "${i}". If you wanted a literal array, use ["literal", [...]].`, 0);
    }
    return void 0 === t ? this.error("'undefined' value invalid. Use null instead.") : "object" == typeof t ? this.error('Bare objects invalid. Use ["literal", {...}] instead.') : this.error(`Expected an array, but found ${typeof t} instead.`);
  }
  concat(t, e, r) {
    const i = "number" == typeof t ? this.path.concat(t) : this.path, s = r ? this.scope.concat(r) : this.scope;
    return new ParsingContext(this.registry, i, e || null, s, this.errors);
  }
  error(t, ...e) {
    const r = `${this.key}${e.map((t4) => `[${t4}]`).join("")}`;
    this.errors.push(new ParsingError(r, t));
  }
  checkSubtype(t, e) {
    const r = checkSubtype(t, e);
    return r && this.error(r), r;
  }
}
function isConstant(t) {
  if (t instanceof Var)
    return isConstant(t.boundExpression);
  if (t instanceof CompoundExpression && "error" === t.name)
    return false;
  if (t instanceof CollatorExpression)
    return false;
  if (t instanceof Within)
    return false;
  const e = t instanceof Coercion || t instanceof Assertion;
  let r = true;
  return t.eachChild((t4) => {
    r = e ? r && isConstant(t4) : r && t4 instanceof Literal;
  }), !!r && (isFeatureConstant(t) && isGlobalPropertyConstant(t, ["zoom", "heatmap-density", "line-progress", "sky-radial-progress", "accumulated", "is-supported-script", "pitch", "distance-from-center"]));
}
const _CompoundExpression = class {
  constructor(t, e, r, i) {
    __publicField(this, "name");
    __publicField(this, "typ");
    __publicField(this, "_evaluate");
    __publicField(this, "args");
    this.name = t, this.type = e, this._evaluate = r, this.args = i;
  }
  evaluate(t) {
    return this._evaluate(t, this.args);
  }
  eachChild(t) {
    this.args.forEach(t);
  }
  outputDefined() {
    return false;
  }
  serialize() {
    return [this.name].concat(this.args.map((t) => t.serialize()));
  }
  static parse(t, e) {
    const r = t[0], i = _CompoundExpression.definitions[r];
    if (!i)
      return e.error(`Unknown expression "${r}". If you wanted a literal array, use ["literal", [...]].`, 0);
    const s = Array.isArray(i) ? i[0] : i.type, n = Array.isArray(i) ? [[i[1], i[2]]] : i.overloads, a = n.filter(([e2]) => !Array.isArray(e2) || e2.length === t.length - 1);
    let o = null;
    for (const [i2, n2] of a) {
      o = new ParsingContext(e.registry, e.path, null, e.scope);
      const a2 = [];
      let h = false;
      for (let e2 = 1; e2 < t.length; e2++) {
        const r2 = t[e2], s2 = Array.isArray(i2) ? i2[e2 - 1] : i2.type, n3 = o.parse(r2, 1 + a2.length, s2);
        if (!n3) {
          h = true;
          break;
        }
        a2.push(n3);
      }
      if (!h)
        if (Array.isArray(i2) && i2.length !== a2.length)
          o.error(`Expected ${i2.length} arguments, but found ${a2.length} instead.`);
        else {
          for (let t4 = 0; t4 < a2.length; t4++) {
            const e2 = Array.isArray(i2) ? i2[t4] : i2.type, r2 = a2[t4];
            o.concat(t4 + 1).checkSubtype(e2, r2.type);
          }
          if (0 === o.errors.length)
            return new _CompoundExpression(r, s, n2, a2);
        }
    }
    if (assert(!o || o.errors.length > 0), 1 === a.length)
      e.errors.push(...o.errors);
    else {
      const r2 = (a.length ? a : n).map(([t4]) => stringifySignature(t4)).join(" | "), i2 = [];
      for (let r3 = 1; r3 < t.length; r3++) {
        const s2 = e.parse(t[r3], 1 + i2.length);
        if (!s2)
          return null;
        i2.push(toString$1(s2.type));
      }
      e.error(`Expected arguments of type ${r2}, but found (${i2.join(", ")}) instead.`);
    }
    return null;
  }
  static register(t, e) {
    assert(!_CompoundExpression.definitions), _CompoundExpression.definitions = e;
    for (const r in e)
      t[r] = _CompoundExpression;
  }
};
let CompoundExpression = _CompoundExpression;
__publicField(CompoundExpression, "definitions");
function stringifySignature(t) {
  return Array.isArray(t) ? `(${t.map(toString$1).join(", ")})` : `(${toString$1(t.type)}...)`;
}
function isFeatureConstant(t) {
  if (t instanceof CompoundExpression) {
    if ("get" === t.name && 1 === t.args.length)
      return false;
    if ("feature-state" === t.name)
      return false;
    if ("has" === t.name && 1 === t.args.length)
      return false;
    if ("properties" === t.name || "geometry-type" === t.name || "id" === t.name)
      return false;
    if (/^filter-/.test(t.name))
      return false;
  }
  if (t instanceof Within)
    return false;
  let e = true;
  return t.eachChild((t4) => {
    e && !isFeatureConstant(t4) && (e = false);
  }), e;
}
function isStateConstant(t) {
  if (t instanceof CompoundExpression && "feature-state" === t.name)
    return false;
  let e = true;
  return t.eachChild((t4) => {
    e && !isStateConstant(t4) && (e = false);
  }), e;
}
function isGlobalPropertyConstant(t, e) {
  if (t instanceof CompoundExpression && e.indexOf(t.name) >= 0)
    return false;
  let r = true;
  return t.eachChild((t4) => {
    r && !isGlobalPropertyConstant(t4, e) && (r = false);
  }), r;
}
class Let {
  constructor(t, e) {
    __publicField(this, "type");
    __publicField(this, "bindings");
    __publicField(this, "result");
    this.type = e.type, this.bindings = [].concat(t), this.result = e;
  }
  evaluate(t) {
    return this.result.evaluate(t);
  }
  eachChild(t) {
    for (const e of this.bindings)
      t(e[1]);
    t(this.result);
  }
  static parse(t, e) {
    if (t.length < 4)
      return e.error(`Expected at least 3 arguments, but found ${t.length - 1} instead.`);
    const r = [];
    for (let i2 = 1; i2 < t.length - 1; i2 += 2) {
      const s = t[i2];
      if ("string" != typeof s)
        return e.error(`Expected string, but found ${typeof s} instead.`, i2);
      if (/[^a-zA-Z0-9_]/.test(s))
        return e.error("Variable names must contain only alphanumeric characters or '_'.", i2);
      const n = e.parse(t[i2 + 1], i2 + 1);
      if (!n)
        return null;
      r.push([s, n]);
    }
    const i = e.parse(t[t.length - 1], t.length - 1, e.expectedType, r);
    return i ? new Let(r, i) : null;
  }
  outputDefined() {
    return this.result.outputDefined();
  }
  serialize() {
    const t = ["let"];
    for (const [e, r] of this.bindings)
      t.push(e, r.serialize());
    return t.push(this.result.serialize()), t;
  }
}
class At {
  constructor(t, e, r) {
    __publicField(this, "type");
    __publicField(this, "index");
    __publicField(this, "input");
    this.type = t, this.index = e, this.input = r;
  }
  static parse(t, e) {
    if (3 !== t.length)
      return e.error(`Expected 2 arguments, but found ${t.length - 1} instead.`);
    const r = e.parse(t[1], 1, NumberType), i = e.parse(t[2], 2, array(e.expectedType || ValueType));
    if (!r || !i)
      return null;
    const s = i.type;
    return new At(s.itemType, r, i);
  }
  evaluate(t) {
    const e = this.index.evaluate(t), r = this.input.evaluate(t);
    if (e < 0)
      throw new Error(`Array index out of bounds: ${e} < 0.`);
    if (e >= r.length)
      throw new Error(`Array index out of bounds: ${e} > ${r.length - 1}.`);
    if (e !== Math.floor(e))
      throw new Error(`Array index must be an integer, but found ${e} instead.`);
    return r[e];
  }
  eachChild(t) {
    t(this.index), t(this.input);
  }
  outputDefined() {
    return false;
  }
  serialize() {
    return ["at", this.index.serialize(), this.input.serialize()];
  }
}
class In {
  constructor(t, e) {
    __publicField(this, "type");
    __publicField(this, "needle");
    __publicField(this, "haystack");
    this.type = BooleanType, this.needle = t, this.haystack = e;
  }
  static parse(t, e) {
    if (3 !== t.length)
      return e.error(`Expected 2 arguments, but found ${t.length - 1} instead.`);
    const r = e.parse(t[1], 1, ValueType), i = e.parse(t[2], 2, ValueType);
    return r && i ? isValidType(r.type, [BooleanType, StringType, NumberType, NullType, ValueType]) ? new In(r, i) : e.error(`Expected first argument to be of type boolean, string, number or null, but found ${toString$1(r.type)} instead`) : null;
  }
  evaluate(t) {
    const e = this.needle.evaluate(t), r = this.haystack.evaluate(t);
    if (null == r)
      return false;
    if (!isValidNativeType(e, ["boolean", "string", "number", "null"]))
      throw new Error(`Expected first argument to be of type boolean, string, number or null, but found ${toString$1(typeOf(e))} instead.`);
    if (!isValidNativeType(r, ["string", "array"]))
      throw new Error(`Expected second argument to be of type array or string, but found ${toString$1(typeOf(r))} instead.`);
    return r.indexOf(e) >= 0;
  }
  eachChild(t) {
    t(this.needle), t(this.haystack);
  }
  outputDefined() {
    return true;
  }
  serialize() {
    return ["in", this.needle.serialize(), this.haystack.serialize()];
  }
}
class IndexOf {
  constructor(t, e, r) {
    __publicField(this, "type");
    __publicField(this, "needle");
    __publicField(this, "haystack");
    __publicField(this, "fromIndex");
    this.type = NumberType, this.needle = t, this.haystack = e, this.fromIndex = r;
  }
  static parse(t, e) {
    if (t.length <= 2 || t.length >= 5)
      return e.error(`Expected 3 or 4 arguments, but found ${t.length - 1} instead.`);
    const r = e.parse(t[1], 1, ValueType), i = e.parse(t[2], 2, ValueType);
    if (!r || !i)
      return null;
    if (!isValidType(r.type, [BooleanType, StringType, NumberType, NullType, ValueType]))
      return e.error(`Expected first argument to be of type boolean, string, number or null, but found ${toString$1(r.type)} instead`);
    if (4 === t.length) {
      const s = e.parse(t[3], 3, NumberType);
      return s ? new IndexOf(r, i, s) : null;
    }
    return new IndexOf(r, i);
  }
  evaluate(t) {
    const e = this.needle.evaluate(t), r = this.haystack.evaluate(t);
    if (!isValidNativeType(e, ["boolean", "string", "number", "null"]))
      throw new Error(`Expected first argument to be of type boolean, string, number or null, but found ${toString$1(typeOf(e))} instead.`);
    if (!isValidNativeType(r, ["string", "array"]))
      throw new Error(`Expected second argument to be of type array or string, but found ${toString$1(typeOf(r))} instead.`);
    if (this.fromIndex) {
      const i = this.fromIndex.evaluate(t);
      return r.indexOf(e, i);
    }
    return r.indexOf(e);
  }
  eachChild(t) {
    t(this.needle), t(this.haystack), this.fromIndex && t(this.fromIndex);
  }
  outputDefined() {
    return false;
  }
  serialize() {
    if (null != this.fromIndex && void 0 !== this.fromIndex) {
      const t = this.fromIndex.serialize();
      return ["index-of", this.needle.serialize(), this.haystack.serialize(), t];
    }
    return ["index-of", this.needle.serialize(), this.haystack.serialize()];
  }
}
class Match {
  constructor(t, e, r, i, s, n) {
    __publicField(this, "type");
    __publicField(this, "inputType");
    __publicField(this, "input");
    __publicField(this, "cases");
    __publicField(this, "outputs");
    __publicField(this, "otherwise");
    this.inputType = t, this.type = e, this.input = r, this.cases = i, this.outputs = s, this.otherwise = n;
  }
  static parse(t, e) {
    if (t.length < 5)
      return e.error(`Expected at least 4 arguments, but found only ${t.length - 1}.`);
    if (t.length % 2 != 1)
      return e.error("Expected an even number of arguments.");
    let r, i;
    e.expectedType && "value" !== e.expectedType.kind && (i = e.expectedType);
    const s = {}, n = [];
    for (let a2 = 2; a2 < t.length - 1; a2 += 2) {
      let o2 = t[a2];
      const h = t[a2 + 1];
      Array.isArray(o2) || (o2 = [o2]);
      const l = e.concat(a2);
      if (0 === o2.length)
        return l.error("Expected at least one branch label.");
      for (const t4 of o2) {
        if ("number" != typeof t4 && "string" != typeof t4)
          return l.error("Branch labels must be numbers or strings.");
        if ("number" == typeof t4 && Math.abs(t4) > Number.MAX_SAFE_INTEGER)
          return l.error(`Branch labels must be integers no larger than ${Number.MAX_SAFE_INTEGER}.`);
        if ("number" == typeof t4 && Math.floor(t4) !== t4)
          return l.error("Numeric branch labels must be integer values.");
        if (r) {
          if (l.checkSubtype(r, typeOf(t4)))
            return null;
        } else
          r = typeOf(t4);
        if (void 0 !== s[String(t4)])
          return l.error("Branch labels must be unique.");
        s[String(t4)] = n.length;
      }
      const c = e.parse(h, a2, i);
      if (!c)
        return null;
      i = i || c.type, n.push(c);
    }
    const a = e.parse(t[1], 1, ValueType);
    if (!a)
      return null;
    const o = e.parse(t[t.length - 1], t.length - 1, i);
    return o ? (assert(r && i), "value" !== a.type.kind && e.concat(1).checkSubtype(r, a.type) ? null : new Match(r, i, a, s, n, o)) : null;
  }
  evaluate(t) {
    const e = this.input.evaluate(t);
    return (typeOf(e) === this.inputType && this.outputs[this.cases[e]] || this.otherwise).evaluate(t);
  }
  eachChild(t) {
    t(this.input), this.outputs.forEach(t), t(this.otherwise);
  }
  outputDefined() {
    return this.outputs.every((t) => t.outputDefined()) && this.otherwise.outputDefined();
  }
  serialize() {
    const t = ["match", this.input.serialize()], e = Object.keys(this.cases).sort(), r = [], i = {};
    for (const t4 of e) {
      const e2 = i[this.cases[t4]];
      void 0 === e2 ? (i[this.cases[t4]] = r.length, r.push([this.cases[t4], [t4]])) : r[e2][1].push(t4);
    }
    const s = (t4) => "number" === this.inputType.kind ? Number(t4) : t4;
    for (const [e2, i2] of r)
      1 === i2.length ? t.push(s(i2[0])) : t.push(i2.map(s)), t.push(this.outputs[e2].serialize());
    return t.push(this.otherwise.serialize()), t;
  }
}
class Case {
  constructor(t, e, r) {
    __publicField(this, "type");
    __publicField(this, "branches");
    __publicField(this, "otherwise");
    this.type = t, this.branches = e, this.otherwise = r;
  }
  static parse(t, e) {
    if (t.length < 4)
      return e.error(`Expected at least 3 arguments, but found only ${t.length - 1}.`);
    if (t.length % 2 != 0)
      return e.error("Expected an odd number of arguments.");
    let r;
    e.expectedType && "value" !== e.expectedType.kind && (r = e.expectedType);
    const i = [];
    for (let s2 = 1; s2 < t.length - 1; s2 += 2) {
      const n = e.parse(t[s2], s2, BooleanType);
      if (!n)
        return null;
      const a = e.parse(t[s2 + 1], s2 + 1, r);
      if (!a)
        return null;
      i.push([n, a]), r = r || a.type;
    }
    const s = e.parse(t[t.length - 1], t.length - 1, r);
    return s ? (assert(r), new Case(r, i, s)) : null;
  }
  evaluate(t) {
    for (const [e, r] of this.branches)
      if (e.evaluate(t))
        return r.evaluate(t);
    return this.otherwise.evaluate(t);
  }
  eachChild(t) {
    for (const [e, r] of this.branches)
      t(e), t(r);
    t(this.otherwise);
  }
  outputDefined() {
    return this.branches.every(([t, e]) => e.outputDefined()) && this.otherwise.outputDefined();
  }
  serialize() {
    const t = ["case"];
    return this.eachChild((e) => {
      t.push(e.serialize());
    }), t;
  }
}
class Slice {
  constructor(t, e, r, i) {
    __publicField(this, "type");
    __publicField(this, "input");
    __publicField(this, "beginIndex");
    __publicField(this, "endIndex");
    this.type = t, this.input = e, this.beginIndex = r, this.endIndex = i;
  }
  static parse(t, e) {
    if (t.length <= 2 || t.length >= 5)
      return e.error(`Expected 3 or 4 arguments, but found ${t.length - 1} instead.`);
    const r = e.parse(t[1], 1, ValueType), i = e.parse(t[2], 2, NumberType);
    if (!r || !i)
      return null;
    if (!isValidType(r.type, [array(ValueType), StringType, ValueType]))
      return e.error(`Expected first argument to be of type array or string, but found ${toString$1(r.type)} instead`);
    if (4 === t.length) {
      const s = e.parse(t[3], 3, NumberType);
      return s ? new Slice(r.type, r, i, s) : null;
    }
    return new Slice(r.type, r, i);
  }
  evaluate(t) {
    const e = this.input.evaluate(t), r = this.beginIndex.evaluate(t);
    if (!isValidNativeType(e, ["string", "array"]))
      throw new Error(`Expected first argument to be of type array or string, but found ${toString$1(typeOf(e))} instead.`);
    if (this.endIndex) {
      const i = this.endIndex.evaluate(t);
      return e.slice(r, i);
    }
    return e.slice(r);
  }
  eachChild(t) {
    t(this.input), t(this.beginIndex), this.endIndex && t(this.endIndex);
  }
  outputDefined() {
    return false;
  }
  serialize() {
    if (null != this.endIndex && void 0 !== this.endIndex) {
      const t = this.endIndex.serialize();
      return ["slice", this.input.serialize(), this.beginIndex.serialize(), t];
    }
    return ["slice", this.input.serialize(), this.beginIndex.serialize()];
  }
}
class Step {
  constructor(t, e, r) {
    __publicField(this, "type");
    __publicField(this, "input");
    __publicField(this, "labels");
    __publicField(this, "outputs");
    this.type = t, this.input = e, this.labels = [], this.outputs = [];
    for (const [t4, e2] of r)
      this.labels.push(t4), this.outputs.push(e2);
  }
  static parse(t, e) {
    if (t.length - 1 < 4)
      return e.error(`Expected at least 4 arguments, but found only ${t.length - 1}.`);
    if ((t.length - 1) % 2 != 0)
      return e.error("Expected an even number of arguments.");
    const r = e.parse(t[1], 1, NumberType);
    if (!r)
      return null;
    const i = [];
    let s = null;
    e.expectedType && "value" !== e.expectedType.kind && (s = e.expectedType);
    for (let r2 = 1; r2 < t.length; r2 += 2) {
      const n = 1 === r2 ? -1 / 0 : t[r2], a = t[r2 + 1], o = r2, h = r2 + 1;
      if ("number" != typeof n)
        return e.error('Input/output pairs for "step" expressions must be defined using literal numeric values (not computed expressions) for the input values.', o);
      if (i.length && i[i.length - 1][0] >= n)
        return e.error('Input/output pairs for "step" expressions must be arranged with input values in strictly ascending order.', o);
      const l = e.parse(a, h, s);
      if (!l)
        return null;
      s = s || l.type, i.push([n, l]);
    }
    return new Step(s, r, i);
  }
  evaluate(t) {
    const e = this.labels, r = this.outputs;
    if (1 === e.length)
      return r[0].evaluate(t);
    const i = this.input.evaluate(t);
    if (i <= e[0])
      return r[0].evaluate(t);
    const s = e.length;
    if (i >= e[s - 1])
      return r[s - 1].evaluate(t);
    return r[findStopLessThanOrEqualTo(e, i)].evaluate(t);
  }
  eachChild(t) {
    t(this.input);
    for (const e of this.outputs)
      t(e);
  }
  outputDefined() {
    return this.outputs.every((t) => t.outputDefined());
  }
  serialize() {
    const t = ["step", this.input.serialize()];
    for (let e = 0; e < this.labels.length; e++)
      e > 0 && t.push(this.labels[e]), t.push(this.outputs[e].serialize());
    return t;
  }
}
class Coalesce {
  constructor(t, e) {
    __publicField(this, "type");
    __publicField(this, "args");
    this.type = t, this.args = e;
  }
  static parse(t, e) {
    if (t.length < 2)
      return e.error("Expectected at least one argument.");
    let r = null;
    const i = e.expectedType;
    i && "value" !== i.kind && (r = i);
    const s = [];
    for (const i2 of t.slice(1)) {
      const t4 = e.parse(i2, 1 + s.length, r, void 0, { typeAnnotation: "omit" });
      if (!t4)
        return null;
      r = r || t4.type, s.push(t4);
    }
    assert(r);
    const n = i && s.some((t4) => checkSubtype(i, t4.type));
    return new Coalesce(n ? ValueType : r, s);
  }
  evaluate(t) {
    let e, r = null, i = 0;
    for (const s of this.args) {
      if (i++, r = s.evaluate(t), r && r instanceof ResolvedImage && !r.available && (e || (e = r), r = null, i === this.args.length))
        return e;
      if (null !== r)
        break;
    }
    return r;
  }
  eachChild(t) {
    this.args.forEach(t);
  }
  outputDefined() {
    return this.args.every((t) => t.outputDefined());
  }
  serialize() {
    const t = ["coalesce"];
    return this.eachChild((e) => {
      t.push(e.serialize());
    }), t;
  }
}
function isComparableType(t, e) {
  return "==" === t || "!=" === t ? "boolean" === e.kind || "string" === e.kind || "number" === e.kind || "null" === e.kind || "value" === e.kind : "string" === e.kind || "number" === e.kind || "value" === e.kind;
}
function eq(t, e, r) {
  return e === r;
}
function neq(t, e, r) {
  return e !== r;
}
function lt(t, e, r) {
  return e < r;
}
function gt(t, e, r) {
  return e > r;
}
function lteq(t, e, r) {
  return e <= r;
}
function gteq(t, e, r) {
  return e >= r;
}
function eqCollate(t, e, r, i) {
  return 0 === i.compare(e, r);
}
function neqCollate(t, e, r, i) {
  return !eqCollate(t, e, r, i);
}
function ltCollate(t, e, r, i) {
  return i.compare(e, r) < 0;
}
function gtCollate(t, e, r, i) {
  return i.compare(e, r) > 0;
}
function lteqCollate(t, e, r, i) {
  return i.compare(e, r) <= 0;
}
function gteqCollate(t, e, r, i) {
  return i.compare(e, r) >= 0;
}
function makeComparison(t, e, r) {
  const i = "==" !== t && "!=" !== t;
  return class s {
    constructor(t4, e2, r2) {
      __publicField(this, "type");
      __publicField(this, "lhs");
      __publicField(this, "rhs");
      __publicField(this, "collator");
      __publicField(this, "hasUntypedArgument");
      this.type = BooleanType, this.lhs = t4, this.rhs = e2, this.collator = r2, this.hasUntypedArgument = "value" === t4.type.kind || "value" === e2.type.kind;
    }
    static parse(t4, e2) {
      if (3 !== t4.length && 4 !== t4.length)
        return e2.error("Expected two or three arguments.");
      const r2 = t4[0];
      let n = e2.parse(t4[1], 1, ValueType);
      if (!n)
        return null;
      if (!isComparableType(r2, n.type))
        return e2.concat(1).error(`"${r2}" comparisons are not supported for type '${toString$1(n.type)}'.`);
      let a = e2.parse(t4[2], 2, ValueType);
      if (!a)
        return null;
      if (!isComparableType(r2, a.type))
        return e2.concat(2).error(`"${r2}" comparisons are not supported for type '${toString$1(a.type)}'.`);
      if (n.type.kind !== a.type.kind && "value" !== n.type.kind && "value" !== a.type.kind)
        return e2.error(`Cannot compare types '${toString$1(n.type)}' and '${toString$1(a.type)}'.`);
      i && ("value" === n.type.kind && "value" !== a.type.kind ? n = new Assertion(a.type, [n]) : "value" !== n.type.kind && "value" === a.type.kind && (a = new Assertion(n.type, [a])));
      let o = null;
      if (4 === t4.length) {
        if ("string" !== n.type.kind && "string" !== a.type.kind && "value" !== n.type.kind && "value" !== a.type.kind)
          return e2.error("Cannot use collator to compare non-string types.");
        if (o = e2.parse(t4[3], 3, CollatorType), !o)
          return null;
      }
      return new s(n, a, o);
    }
    evaluate(s2) {
      const n = this.lhs.evaluate(s2), a = this.rhs.evaluate(s2);
      if (i && this.hasUntypedArgument) {
        const e2 = typeOf(n), r2 = typeOf(a);
        if (e2.kind !== r2.kind || "string" !== e2.kind && "number" !== e2.kind)
          throw new Error(`Expected arguments for "${t}" to be (string, string) or (number, number),
                        but found (${e2.kind}, ${r2.kind}) instead.`);
      }
      if (this.collator && !i && this.hasUntypedArgument) {
        const t4 = typeOf(n), r2 = typeOf(a);
        if ("string" !== t4.kind || "string" !== r2.kind)
          return e(s2, n, a);
      }
      return this.collator ? r(s2, n, a, this.collator.evaluate(s2)) : e(s2, n, a);
    }
    eachChild(t4) {
      t4(this.lhs), t4(this.rhs), this.collator && t4(this.collator);
    }
    outputDefined() {
      return true;
    }
    serialize() {
      const e2 = [t];
      return this.eachChild((t4) => {
        e2.push(t4.serialize());
      }), e2;
    }
  };
}
const Equals = makeComparison("==", eq, eqCollate), NotEquals = makeComparison("!=", neq, neqCollate), LessThan = makeComparison("<", lt, ltCollate), GreaterThan = makeComparison(">", gt, gtCollate), LessThanOrEqual = makeComparison("<=", lteq, lteqCollate), GreaterThanOrEqual = makeComparison(">=", gteq, gteqCollate);
class NumberFormat {
  constructor(t, e, r, i, s, n) {
    __publicField(this, "type");
    __publicField(this, "number");
    __publicField(this, "locale");
    __publicField(this, "currency");
    __publicField(this, "unit");
    __publicField(this, "minFractionDigits");
    __publicField(this, "maxFractionDigits");
    this.type = StringType, this.number = t, this.locale = e, this.currency = r, this.unit = i, this.minFractionDigits = s, this.maxFractionDigits = n;
  }
  static parse(t, e) {
    if (3 !== t.length)
      return e.error("Expected two arguments.");
    const r = e.parse(t[1], 1, NumberType);
    if (!r)
      return null;
    const i = t[2];
    if ("object" != typeof i || Array.isArray(i))
      return e.error("NumberFormat options argument must be an object.");
    let s = null;
    if (i.locale && (s = e.parse(i.locale, 1, StringType), !s))
      return null;
    let n = null;
    if (i.currency && (n = e.parse(i.currency, 1, StringType), !n))
      return null;
    let a = null;
    if (i.unit && (a = e.parse(i.unit, 1, StringType), !a))
      return null;
    let o = null;
    if (i["min-fraction-digits"] && (o = e.parse(i["min-fraction-digits"], 1, NumberType), !o))
      return null;
    let h = null;
    return i["max-fraction-digits"] && (h = e.parse(i["max-fraction-digits"], 1, NumberType), !h) ? null : new NumberFormat(r, s, n, a, o, h);
  }
  evaluate(t) {
    return new Intl.NumberFormat(this.locale ? this.locale.evaluate(t) : [], { style: (this.currency ? "currency" : this.unit && "unit") || "decimal", currency: this.currency ? this.currency.evaluate(t) : void 0, unit: this.unit ? this.unit.evaluate(t) : void 0, minimumFractionDigits: this.minFractionDigits ? this.minFractionDigits.evaluate(t) : void 0, maximumFractionDigits: this.maxFractionDigits ? this.maxFractionDigits.evaluate(t) : void 0 }).format(this.number.evaluate(t));
  }
  eachChild(t) {
    t(this.number), this.locale && t(this.locale), this.currency && t(this.currency), this.unit && t(this.unit), this.minFractionDigits && t(this.minFractionDigits), this.maxFractionDigits && t(this.maxFractionDigits);
  }
  outputDefined() {
    return false;
  }
  serialize() {
    const t = {};
    return this.locale && (t.locale = this.locale.serialize()), this.currency && (t.currency = this.currency.serialize()), this.unit && (t.unit = this.unit.serialize()), this.minFractionDigits && (t["min-fraction-digits"] = this.minFractionDigits.serialize()), this.maxFractionDigits && (t["max-fraction-digits"] = this.maxFractionDigits.serialize()), ["number-format", this.number.serialize(), t];
  }
}
class Length {
  constructor(t) {
    __publicField(this, "type");
    __publicField(this, "input");
    this.type = NumberType, this.input = t;
  }
  static parse(t, e) {
    if (2 !== t.length)
      return e.error(`Expected 1 argument, but found ${t.length - 1} instead.`);
    const r = e.parse(t[1], 1);
    return r ? "array" !== r.type.kind && "string" !== r.type.kind && "value" !== r.type.kind ? e.error(`Expected argument of type string or array, but found ${toString$1(r.type)} instead.`) : new Length(r) : null;
  }
  evaluate(t) {
    const e = this.input.evaluate(t);
    if ("string" == typeof e)
      return e.length;
    if (Array.isArray(e))
      return e.length;
    throw new Error(`Expected value to be of type string or array, but found ${toString$1(typeOf(e))} instead.`);
  }
  eachChild(t) {
    t(this.input);
  }
  outputDefined() {
    return false;
  }
  serialize() {
    const t = ["length"];
    return this.eachChild((e) => {
      t.push(e.serialize());
    }), t;
  }
}
const expressions = { "==": Equals, "!=": NotEquals, ">": GreaterThan, "<": LessThan, ">=": GreaterThanOrEqual, "<=": LessThanOrEqual, array: Assertion, at: At, boolean: Assertion, case: Case, coalesce: Coalesce, collator: CollatorExpression, format: FormatExpression, image: ImageExpression, in: In, "index-of": IndexOf, interpolate: Interpolate, "interpolate-hcl": Interpolate, "interpolate-lab": Interpolate, length: Length, let: Let, literal: Literal, match: Match, number: Assertion, "number-format": NumberFormat, object: Assertion, slice: Slice, step: Step, string: Assertion, "to-boolean": Coercion, "to-color": Coercion, "to-number": Coercion, "to-string": Coercion, var: Var, within: Within };
function rgba(t, [e, r, i, s]) {
  e = e.evaluate(t), r = r.evaluate(t), i = i.evaluate(t);
  const n = s ? s.evaluate(t) : 1, a = validateRGBA(e, r, i, n);
  if (a)
    throw new Error(a);
  return new Color(e / 255 * n, r / 255 * n, i / 255 * n, n);
}
function has(t, e) {
  return t in e;
}
function get(t, e) {
  const r = e[t];
  return void 0 === r ? null : r;
}
function binarySearch(t, e, r, i) {
  for (; r <= i; ) {
    const s = r + i >> 1;
    if (e[s] === t)
      return true;
    e[s] > t ? i = s - 1 : r = s + 1;
  }
  return false;
}
function varargs(t) {
  return { type: t };
}
CompoundExpression.register(expressions, { error: [ErrorType, [StringType], (t, [e]) => {
  throw new Error(e.evaluate(t));
}], typeof: [StringType, [ValueType], (t, [e]) => toString$1(typeOf(e.evaluate(t)))], "to-rgba": [array(NumberType, 4), [ColorType], (t, [e]) => e.evaluate(t).toArray()], rgb: [ColorType, [NumberType, NumberType, NumberType], rgba], rgba: [ColorType, [NumberType, NumberType, NumberType, NumberType], rgba], has: { type: BooleanType, overloads: [[[StringType], (t, [e]) => has(e.evaluate(t), t.properties())], [[StringType, ObjectType], (t, [e, r]) => has(e.evaluate(t), r.evaluate(t))]] }, get: { type: ValueType, overloads: [[[StringType], (t, [e]) => get(e.evaluate(t), t.properties())], [[StringType, ObjectType], (t, [e, r]) => get(e.evaluate(t), r.evaluate(t))]] }, "feature-state": [ValueType, [StringType], (t, [e]) => get(e.evaluate(t), t.featureState || {})], properties: [ObjectType, [], (t) => t.properties()], "geometry-type": [StringType, [], (t) => t.geometryType()], id: [ValueType, [], (t) => t.id()], zoom: [NumberType, [], (t) => t.globals.zoom], pitch: [NumberType, [], (t) => t.globals.pitch || 0], "distance-from-center": [NumberType, [], (t) => t.distanceFromCenter()], "heatmap-density": [NumberType, [], (t) => t.globals.heatmapDensity || 0], "line-progress": [NumberType, [], (t) => t.globals.lineProgress || 0], "sky-radial-progress": [NumberType, [], (t) => t.globals.skyRadialProgress || 0], accumulated: [ValueType, [], (t) => void 0 === t.globals.accumulated ? null : t.globals.accumulated], "+": [NumberType, varargs(NumberType), (t, e) => {
  let r = 0;
  for (const i of e)
    r += i.evaluate(t);
  return r;
}], "*": [NumberType, varargs(NumberType), (t, e) => {
  let r = 1;
  for (const i of e)
    r *= i.evaluate(t);
  return r;
}], "-": { type: NumberType, overloads: [[[NumberType, NumberType], (t, [e, r]) => e.evaluate(t) - r.evaluate(t)], [[NumberType], (t, [e]) => -e.evaluate(t)]] }, "/": [NumberType, [NumberType, NumberType], (t, [e, r]) => e.evaluate(t) / r.evaluate(t)], "%": [NumberType, [NumberType, NumberType], (t, [e, r]) => e.evaluate(t) % r.evaluate(t)], ln2: [NumberType, [], () => Math.LN2], pi: [NumberType, [], () => Math.PI], e: [NumberType, [], () => Math.E], "^": [NumberType, [NumberType, NumberType], (t, [e, r]) => Math.pow(e.evaluate(t), r.evaluate(t))], sqrt: [NumberType, [NumberType], (t, [e]) => Math.sqrt(e.evaluate(t))], log10: [NumberType, [NumberType], (t, [e]) => Math.log(e.evaluate(t)) / Math.LN10], ln: [NumberType, [NumberType], (t, [e]) => Math.log(e.evaluate(t))], log2: [NumberType, [NumberType], (t, [e]) => Math.log(e.evaluate(t)) / Math.LN2], sin: [NumberType, [NumberType], (t, [e]) => Math.sin(e.evaluate(t))], cos: [NumberType, [NumberType], (t, [e]) => Math.cos(e.evaluate(t))], tan: [NumberType, [NumberType], (t, [e]) => Math.tan(e.evaluate(t))], asin: [NumberType, [NumberType], (t, [e]) => Math.asin(e.evaluate(t))], acos: [NumberType, [NumberType], (t, [e]) => Math.acos(e.evaluate(t))], atan: [NumberType, [NumberType], (t, [e]) => Math.atan(e.evaluate(t))], min: [NumberType, varargs(NumberType), (t, e) => Math.min(...e.map((e2) => e2.evaluate(t)))], max: [NumberType, varargs(NumberType), (t, e) => Math.max(...e.map((e2) => e2.evaluate(t)))], abs: [NumberType, [NumberType], (t, [e]) => Math.abs(e.evaluate(t))], round: [NumberType, [NumberType], (t, [e]) => {
  const r = e.evaluate(t);
  return r < 0 ? -Math.round(-r) : Math.round(r);
}], floor: [NumberType, [NumberType], (t, [e]) => Math.floor(e.evaluate(t))], ceil: [NumberType, [NumberType], (t, [e]) => Math.ceil(e.evaluate(t))], "filter-==": [BooleanType, [StringType, ValueType], (t, [e, r]) => t.properties()[e.value] === r.value], "filter-id-==": [BooleanType, [ValueType], (t, [e]) => t.id() === e.value], "filter-type-==": [BooleanType, [StringType], (t, [e]) => t.geometryType() === e.value], "filter-<": [BooleanType, [StringType, ValueType], (t, [e, r]) => {
  const i = t.properties()[e.value], s = r.value;
  return typeof i == typeof s && i < s;
}], "filter-id-<": [BooleanType, [ValueType], (t, [e]) => {
  const r = t.id(), i = e.value;
  return typeof r == typeof i && r < i;
}], "filter->": [BooleanType, [StringType, ValueType], (t, [e, r]) => {
  const i = t.properties()[e.value], s = r.value;
  return typeof i == typeof s && i > s;
}], "filter-id->": [BooleanType, [ValueType], (t, [e]) => {
  const r = t.id(), i = e.value;
  return typeof r == typeof i && r > i;
}], "filter-<=": [BooleanType, [StringType, ValueType], (t, [e, r]) => {
  const i = t.properties()[e.value], s = r.value;
  return typeof i == typeof s && i <= s;
}], "filter-id-<=": [BooleanType, [ValueType], (t, [e]) => {
  const r = t.id(), i = e.value;
  return typeof r == typeof i && r <= i;
}], "filter->=": [BooleanType, [StringType, ValueType], (t, [e, r]) => {
  const i = t.properties()[e.value], s = r.value;
  return typeof i == typeof s && i >= s;
}], "filter-id->=": [BooleanType, [ValueType], (t, [e]) => {
  const r = t.id(), i = e.value;
  return typeof r == typeof i && r >= i;
}], "filter-has": [BooleanType, [ValueType], (t, [e]) => e.value in t.properties()], "filter-has-id": [BooleanType, [], (t) => null !== t.id() && void 0 !== t.id()], "filter-type-in": [BooleanType, [array(StringType)], (t, [e]) => e.value.indexOf(t.geometryType()) >= 0], "filter-id-in": [BooleanType, [array(ValueType)], (t, [e]) => e.value.indexOf(t.id()) >= 0], "filter-in-small": [BooleanType, [StringType, array(ValueType)], (t, [e, r]) => r.value.indexOf(t.properties()[e.value]) >= 0], "filter-in-large": [BooleanType, [StringType, array(ValueType)], (t, [e, r]) => binarySearch(t.properties()[e.value], r.value, 0, r.value.length - 1)], all: { type: BooleanType, overloads: [[[BooleanType, BooleanType], (t, [e, r]) => e.evaluate(t) && r.evaluate(t)], [varargs(BooleanType), (t, e) => {
  for (const r of e)
    if (!r.evaluate(t))
      return false;
  return true;
}]] }, any: { type: BooleanType, overloads: [[[BooleanType, BooleanType], (t, [e, r]) => e.evaluate(t) || r.evaluate(t)], [varargs(BooleanType), (t, e) => {
  for (const r of e)
    if (r.evaluate(t))
      return true;
  return false;
}]] }, "!": [BooleanType, [BooleanType], (t, [e]) => !e.evaluate(t)], "is-supported-script": [BooleanType, [StringType], (t, [e]) => {
  const r = t.globals && t.globals.isSupportedScript;
  return !r || r(e.evaluate(t));
}], upcase: [StringType, [StringType], (t, [e]) => e.evaluate(t).toUpperCase()], downcase: [StringType, [StringType], (t, [e]) => e.evaluate(t).toLowerCase()], concat: [StringType, varargs(ValueType), (t, e) => e.map((e2) => toString(e2.evaluate(t))).join("")], "resolved-locale": [StringType, [CollatorType], (t, [e]) => e.evaluate(t).resolvedLocale()] });
class StyleExpression {
  constructor(t, e) {
    __publicField(this, "expression");
    __publicField(this, "_evaluator");
    __publicField(this, "_defaultValue");
    __publicField(this, "_warningHistory");
    __publicField(this, "_enumValues");
    this.expression = t, this._warningHistory = {}, this._evaluator = new EvaluationContext(), this._defaultValue = e ? getDefaultValue(e) : null, this._enumValues = e && "enum" === e.type ? e.values : null;
  }
  evaluateWithoutErrorHandling(t, e, r, i, s, n, a, o) {
    return this._evaluator.globals = t, this._evaluator.feature = e, this._evaluator.featureState = r, this._evaluator.canonical = i || null, this._evaluator.availableImages = s || null, this._evaluator.formattedSection = n, this._evaluator.featureTileCoord = a || null, this._evaluator.featureDistanceData = o || null, this.expression.evaluate(this._evaluator);
  }
  evaluate(t, e, r, i, s, n, a, o) {
    this._evaluator.globals = t, this._evaluator.feature = e || null, this._evaluator.featureState = r || null, this._evaluator.canonical = i || null, this._evaluator.availableImages = s || null, this._evaluator.formattedSection = n || null, this._evaluator.featureTileCoord = a || null, this._evaluator.featureDistanceData = o || null;
    try {
      const t4 = this.expression.evaluate(this._evaluator);
      if (null == t4 || "number" == typeof t4 && t4 != t4)
        return this._defaultValue;
      if (this._enumValues && !(t4 in this._enumValues))
        throw new Error(`Expected value to be one of ${Object.keys(this._enumValues).map((t5) => JSON.stringify(t5)).join(", ")}, but found ${JSON.stringify(t4)} instead.`);
      return t4;
    } catch (t4) {
      return this._warningHistory[t4.message] || (this._warningHistory[t4.message] = true, "undefined" != typeof console && console.warn(t4.message)), this._defaultValue;
    }
  }
}
class ZoomConstantExpression {
  constructor(t, e) {
    __publicField(this, "kind");
    __publicField(this, "isStateDependent");
    __publicField(this, "_styleExpression");
    this.kind = t, this._styleExpression = e, this.isStateDependent = "constant" !== t && !isStateConstant(e.expression);
  }
  evaluateWithoutErrorHandling(t, e, r, i, s, n) {
    return this._styleExpression.evaluateWithoutErrorHandling(t, e, r, i, s, n);
  }
  evaluate(t, e, r, i, s, n) {
    return this._styleExpression.evaluate(t, e, r, i, s, n);
  }
}
class ZoomDependentExpression {
  constructor(t, e, r, i) {
    __publicField(this, "kind");
    __publicField(this, "zoomStops");
    __publicField(this, "isStateDependent");
    __publicField(this, "_styleExpression");
    __publicField(this, "interpolationType");
    this.kind = t, this.zoomStops = r, this._styleExpression = e, this.isStateDependent = "camera" !== t && !isStateConstant(e.expression), this.interpolationType = i;
  }
  evaluateWithoutErrorHandling(t, e, r, i, s, n) {
    return this._styleExpression.evaluateWithoutErrorHandling(t, e, r, i, s, n);
  }
  evaluate(t, e, r, i, s, n) {
    return this._styleExpression.evaluate(t, e, r, i, s, n);
  }
  interpolationFactor(t, e, r) {
    return this.interpolationType ? Interpolate.interpolationFactor(this.interpolationType, t, e, r) : 0;
  }
}
function isExpression(t) {
  return Array.isArray(t) && t.length > 0 && "string" == typeof t[0] && t[0] in expressions;
}
function createPropertyExpression(t, e) {
  if ("error" === (t = createExpression(t, e)).result)
    return t;
  const r = t.value.expression, i = isFeatureConstant(r);
  if (!i && !supportsPropertyExpression(e))
    return { result: "error", value: new Error("data expressions not supported") };
  const s = isGlobalPropertyConstant(r, ["zoom", "pitch", "distance-from-center"]);
  if (!s && !supportsZoomExpression(e))
    return { result: "error", value: new Error("zoom expressions not supported") };
  const n = findZoomCurve(r);
  if (!n && !s)
    return { result: "error", value: new Error('"zoom" expression may only be used as input to a top-level "step" or "interpolate" expression.') };
  if (n instanceof ParsingError)
    return { result: "error", value: n };
  if (n instanceof Interpolate && !supportsInterpolation(e))
    return { result: "error", value: new Error('"interpolate" expressions cannot be used with this property') };
  if (!n)
    return { result: "success", value: new ZoomConstantExpression(i ? "constant" : "source", t.value) };
  const a = n instanceof Interpolate ? n.interpolation : void 0;
  return { result: "success", value: new ZoomDependentExpression(i ? "camera" : "composite", t.value, n.labels, a) };
}
function createExpression(t, e) {
  const r = new ParsingContext(expressions, [], e ? getExpectedType(e) : void 0), i = r.parse(t, void 0, void 0, void 0, e && "string" === e.type ? { typeAnnotation: "coerce" } : void 0);
  return i ? { result: "success", value: new StyleExpression(i, e) } : (assert(r.errors.length > 0), { result: "error", value: r.errors });
}
function findZoomCurve(t) {
  let e = null;
  if (t instanceof Let)
    e = findZoomCurve(t.result);
  else if (t instanceof Coalesce) {
    for (const r of t.args)
      if (e = findZoomCurve(r), e)
        break;
  } else
    (t instanceof Step || t instanceof Interpolate) && t.input instanceof CompoundExpression && "zoom" === t.input.name && (e = t);
  return e instanceof ParsingError || t.eachChild((t4) => {
    const r = findZoomCurve(t4);
    r instanceof ParsingError ? e = r : !e && r ? e = new ParsingError("", '"zoom" expression may only be used as input to a top-level "step" or "interpolate" expression.') : e && r && e !== r && (e = new ParsingError("", 'Only one zoom-based "step" or "interpolate" subexpression may be used in an expression.'));
  }), e;
}
function getExpectedType(t) {
  const e = { color: ColorType, string: StringType, number: NumberType, enum: StringType, boolean: BooleanType, formatted: FormattedType, resolvedImage: ResolvedImageType };
  return "array" === t.type ? array(e[t.value] || ValueType, t.length) : e[t.type];
}
function getDefaultValue(t) {
  return "color" === t.type && (isFunction(t.default) || Array.isArray(t.default)) ? new Color(0, 0, 0, 0) : "color" === t.type ? Color.parse(t.default) || null : void 0 === t.default ? null : t.default;
}
function isExpressionFilter(t) {
  if (true === t || false === t)
    return true;
  if (!Array.isArray(t) || 0 === t.length)
    return false;
  switch (t[0]) {
    case "has":
      return t.length >= 2 && "$id" !== t[1] && "$type" !== t[1];
    case "in":
      return t.length >= 3 && ("string" != typeof t[1] || Array.isArray(t[2]));
    case "!in":
    case "!has":
    case "none":
      return false;
    case "==":
    case "!=":
    case ">":
    case ">=":
    case "<":
    case "<=":
      return 3 !== t.length || Array.isArray(t[1]) || Array.isArray(t[2]);
    case "any":
    case "all":
      for (const e of t.slice(1))
        if (!isExpressionFilter(e) && "boolean" != typeof e)
          return false;
      return true;
    default:
      return true;
  }
}
function createFilter(t, e, r = "fill") {
  if (null == e)
    return { filter: () => true, needGeometry: false, needFeature: false };
  isExpressionFilter(e) || (e = convertFilter(e));
  const i = e;
  let s = true;
  try {
    s = extractStaticFilter(i);
  } catch (t4) {
    console.warn(`Failed to extract static filter. Filter will continue working, but at higher memory usage and slower framerate.
This is most likely a bug, please report this via https://github.com/mapbox/mapbox-gl-js/issues/new?assignees=&labels=&template=Bug_report.md
and paste the contents of this message in the report.
Thank you!
Filter Expression:
${JSON.stringify(i, null, 2)}
        `);
  }
  const n = t[`filter_${r}`], a = createExpression(s, n);
  let o = null;
  if ("error" === a.result)
    throw new Error(a.value.map((t4) => `${t4.key}: ${t4.message}`).join(", "));
  o = (t4, e2, r2) => a.value.evaluate(t4, e2, {}, r2);
  let h = null, l = null;
  if (s !== i) {
    const t4 = createExpression(i, n);
    if ("error" === t4.result)
      throw new Error(t4.value.map((t5) => `${t5.key}: ${t5.message}`).join(", "));
    h = (e2, r2, i2, s2, n2) => t4.value.evaluate(e2, r2, {}, i2, void 0, void 0, s2, n2), l = !isFeatureConstant(t4.value.expression);
  }
  return { filter: o, dynamicFilter: h || void 0, needGeometry: geometryNeeded(s), needFeature: !!l };
}
function extractStaticFilter(t) {
  if (!isDynamicFilter(t))
    return t;
  let e = deepUnbundle(t);
  return unionDynamicBranches(e), e = collapseDynamicBooleanExpressions(e), e;
}
function collapseDynamicBooleanExpressions(t) {
  if (!Array.isArray(t))
    return t;
  const e = collapsedExpression(t);
  return true === e ? e : e.map((t4) => collapseDynamicBooleanExpressions(t4));
}
function unionDynamicBranches(t) {
  let e = false;
  const r = [];
  if ("case" === t[0]) {
    for (let i = 1; i < t.length - 1; i += 2)
      e = e || isDynamicFilter(t[i]), r.push(t[i + 1]);
    r.push(t[t.length - 1]);
  } else if ("match" === t[0]) {
    e = e || isDynamicFilter(t[1]);
    for (let e2 = 2; e2 < t.length - 1; e2 += 2)
      r.push(t[e2 + 1]);
    r.push(t[t.length - 1]);
  } else if ("step" === t[0]) {
    e = e || isDynamicFilter(t[1]);
    for (let e2 = 1; e2 < t.length - 1; e2 += 2)
      r.push(t[e2 + 1]);
  }
  e && (t.length = 0, t.push("any", ...r));
  for (let e2 = 1; e2 < t.length; e2++)
    unionDynamicBranches(t[e2]);
}
function isDynamicFilter(t) {
  if (!Array.isArray(t))
    return false;
  if (isRootExpressionDynamic(t[0]))
    return true;
  for (let e = 1; e < t.length; e++) {
    if (isDynamicFilter(t[e]))
      return true;
  }
  return false;
}
function isRootExpressionDynamic(t) {
  return "pitch" === t || "distance-from-center" === t;
}
const dynamicConditionExpressions = /* @__PURE__ */ new Set(["in", "==", "!=", ">", ">=", "<", "<=", "to-boolean"]);
function collapsedExpression(t) {
  if (dynamicConditionExpressions.has(t[0]))
    for (let e = 1; e < t.length; e++) {
      if (isDynamicFilter(t[e]))
        return true;
    }
  return t;
}
function compare(t, e) {
  return t < e ? -1 : t > e ? 1 : 0;
}
function geometryNeeded(t) {
  if (!Array.isArray(t))
    return false;
  if ("within" === t[0])
    return true;
  for (let e = 1; e < t.length; e++)
    if (geometryNeeded(t[e]))
      return true;
  return false;
}
function convertFilter(t) {
  if (!t)
    return true;
  const e = t[0];
  if (t.length <= 1)
    return "any" !== e;
  return "==" === e ? convertComparisonOp(t[1], t[2], "==") : "!=" === e ? convertNegation(convertComparisonOp(t[1], t[2], "==")) : "<" === e || ">" === e || "<=" === e || ">=" === e ? convertComparisonOp(t[1], t[2], e) : "any" === e ? convertDisjunctionOp(t.slice(1)) : "all" === e ? ["all"].concat(t.slice(1).map(convertFilter)) : "none" === e ? ["all"].concat(t.slice(1).map(convertFilter).map(convertNegation)) : "in" === e ? convertInOp(t[1], t.slice(2)) : "!in" === e ? convertNegation(convertInOp(t[1], t.slice(2))) : "has" === e ? convertHasOp(t[1]) : "!has" === e ? convertNegation(convertHasOp(t[1])) : "within" !== e || t;
}
function convertComparisonOp(t, e, r) {
  switch (t) {
    case "$type":
      return [`filter-type-${r}`, e];
    case "$id":
      return [`filter-id-${r}`, e];
    default:
      return [`filter-${r}`, t, e];
  }
}
function convertDisjunctionOp(t) {
  return ["any"].concat(t.map(convertFilter));
}
function convertInOp(t, e) {
  if (0 === e.length)
    return false;
  switch (t) {
    case "$type":
      return ["filter-type-in", ["literal", e]];
    case "$id":
      return ["filter-id-in", ["literal", e]];
    default:
      return e.length > 200 && !e.some((t4) => typeof t4 != typeof e[0]) ? ["filter-in-large", t, ["literal", e.sort(compare)]] : ["filter-in-small", t, ["literal", e]];
  }
}
function convertHasOp(t) {
  switch (t) {
    case "$type":
      return true;
    case "$id":
      return ["filter-has-id"];
    default:
      return ["filter-has", t];
  }
}
function convertNegation(t) {
  return ["!", t];
}
function convertLiteral(t) {
  return "object" == typeof t ? ["literal", t] : t;
}
function convertFunction(t, e) {
  let r = t.stops;
  if (!r)
    return convertIdentityFunction(t, e);
  const i = r && "object" == typeof r[0][0], s = i || void 0 !== t.property, n = i || !s;
  return r = r.map((t4) => !s && e.tokens && "string" == typeof t4[1] ? [t4[0], convertTokenString(t4[1])] : [t4[0], convertLiteral(t4[1])]), i ? convertZoomAndPropertyFunction(t, e, r) : n ? convertZoomFunction(t, e, r) : convertPropertyFunction(t, e, r);
}
function convertIdentityFunction(t, e) {
  const r = ["get", t.property];
  if (void 0 === t.default)
    return "string" === e.type ? ["string", r] : r;
  if ("enum" === e.type)
    return ["match", r, Object.keys(e.values), r, t.default];
  const i = ["color" === e.type ? "to-color" : e.type, r, convertLiteral(t.default)];
  return "array" === e.type && i.splice(1, 0, e.value, e.length || null), i;
}
function getInterpolateOperator(t) {
  switch (t.colorSpace) {
    case "hcl":
      return "interpolate-hcl";
    case "lab":
      return "interpolate-lab";
    default:
      return "interpolate";
  }
}
function convertZoomAndPropertyFunction(t, e, r) {
  const i = {}, s = {}, n = [];
  for (let e2 = 0; e2 < r.length; e2++) {
    const a2 = r[e2], o = a2[0].zoom;
    void 0 === i[o] && (i[o] = { zoom: o, type: t.type, property: t.property, default: t.default }, s[o] = [], n.push(o)), s[o].push([a2[0].value, a2[1]]);
  }
  if ("exponential" === getFunctionType({}, e)) {
    const r2 = [getInterpolateOperator(t), ["linear"], ["zoom"]];
    for (const t4 of n) {
      appendStopPair(r2, t4, convertPropertyFunction(i[t4], e, s[t4]), false);
    }
    return r2;
  }
  const a = ["step", ["zoom"]];
  for (const t4 of n) {
    appendStopPair(a, t4, convertPropertyFunction(i[t4], e, s[t4]), true);
  }
  return fixupDegenerateStepCurve(a), a;
}
function coalesce(t, e) {
  return void 0 !== t ? t : void 0 !== e ? e : void 0;
}
function getFallback(t, e) {
  const r = convertLiteral(coalesce(t.default, e.default));
  return void 0 === r && "resolvedImage" === e.type ? "" : r;
}
function convertPropertyFunction(t, e, r) {
  const i = getFunctionType(t, e), s = ["get", t.property];
  if ("categorical" === i && "boolean" == typeof r[0][0]) {
    assert(t.stops.length > 0 && t.stops.length <= 2);
    const i2 = ["case"];
    for (const t4 of r)
      i2.push(["==", s, t4[0]], t4[1]);
    return i2.push(getFallback(t, e)), i2;
  }
  if ("categorical" === i) {
    const i2 = ["match", s];
    for (const t4 of r)
      appendStopPair(i2, t4[0], t4[1], false);
    return i2.push(getFallback(t, e)), i2;
  }
  if ("interval" === i) {
    const e2 = ["step", ["number", s]];
    for (const t4 of r)
      appendStopPair(e2, t4[0], t4[1], true);
    return fixupDegenerateStepCurve(e2), void 0 === t.default ? e2 : ["case", ["==", ["typeof", s], "number"], e2, convertLiteral(t.default)];
  }
  if ("exponential" === i) {
    const e2 = void 0 !== t.base ? t.base : 1, i2 = [getInterpolateOperator(t), 1 === e2 ? ["linear"] : ["exponential", e2], ["number", s]];
    for (const t4 of r)
      appendStopPair(i2, t4[0], t4[1], false);
    return void 0 === t.default ? i2 : ["case", ["==", ["typeof", s], "number"], i2, convertLiteral(t.default)];
  }
  throw new Error(`Unknown property function type ${i}`);
}
function convertZoomFunction(t, e, r, i = ["zoom"]) {
  const s = getFunctionType(t, e);
  let n, a = false;
  if ("interval" === s)
    n = ["step", i], a = true;
  else {
    if ("exponential" !== s)
      throw new Error(`Unknown zoom function type "${s}"`);
    {
      const e2 = void 0 !== t.base ? t.base : 1;
      n = [getInterpolateOperator(t), 1 === e2 ? ["linear"] : ["exponential", e2], i];
    }
  }
  for (const t4 of r)
    appendStopPair(n, t4[0], t4[1], a);
  return fixupDegenerateStepCurve(n), n;
}
function fixupDegenerateStepCurve(t) {
  "step" === t[0] && 3 === t.length && (t.push(0), t.push(t[3]));
}
function appendStopPair(t, e, r, i) {
  t.length > 3 && e === t[t.length - 2] || (i && 2 === t.length || t.push(e), t.push(r));
}
function getFunctionType(t, e) {
  return t.type ? t.type : (assert(e.expression), e.expression.interpolated ? "exponential" : "interval");
}
function convertTokenString(t) {
  const e = ["concat"], r = /{([^{}]+)}/g;
  let i = 0;
  for (let s = r.exec(t); null !== s; s = r.exec(t)) {
    const n = t.slice(i, r.lastIndex - s[0].length);
    i = r.lastIndex, n.length > 0 && e.push(n), e.push(["get", s[1]]);
  }
  if (1 === e.length)
    return t;
  if (i < t.length)
    e.push(t.slice(i));
  else if (2 === e.length)
    return ["to-string", e[1]];
  return e;
}
class MapboxStyle {
  constructor(t, e, r) {
    return MapboxStyle.instance || (this._styleJSON = t, this._spec = e, this.zoomObject = { zoom: 0 }, this.functionCache = {}, this.filterCache = {}, this.featureState = {}, this.spriteData = r, MapboxStyle.instance = this), MapboxStyle.instance;
  }
  setStyleJSON(t) {
    this._styleJSON = t;
  }
  getStyleJSON() {
    return this._styleJSON;
  }
  setSpec(t) {
    this._spec = t;
  }
  getSpec() {
    return this._spec;
  }
  expressionData(t, e) {
    const r = createPropertyExpression(t, e);
    if ("error" === r.result)
      throw new Error(r.value.map((t4) => `${t4.key}: ${t4.message}`).join(", "));
    return r.value;
  }
  getValue(t, e, r, i, s) {
    const n = t.id;
    this.functionCache[n] || (this.functionCache[n] = {});
    const a = this.functionCache[n];
    if (!a[r]) {
      let i2 = (t[e] || {})[r];
      const s2 = this._spec[`${e}_${t.type}`][r];
      void 0 === i2 && (i2 = s2.default);
      let n2 = isExpression(i2);
      if (!n2 && isFunction(i2) && (i2 = convertFunction(i2, s2), n2 = true), n2) {
        const t4 = this.expressionData(i2, s2);
        a[r] = t4.evaluate.bind(t4);
      } else
        "color" === s2.type && (i2 = Color.parse(i2)), a[r] = () => i2;
    }
    return this.zoomObject.zoom = i, a[r](this.zoomObject, s, this.featureState);
  }
  evaluateFilter(t, e, r, i) {
    return t in this.filterCache || (this.filterCache[t] = createFilter(this._spec, e).filter), this.zoomObject.zoom = i, this.filterCache[t](this.zoomObject, r);
  }
}
const defaultGeoBoundingBox = new Box3(new Vector3$1(-180, -90, -100), new Vector3$1(180, 90, 100));
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
  geoBoxToProjectedBox(t, e, r = true) {
    if (e || (e = new Box3()), this.projectBoundingBoxMethod === projectBoundingBoxMethods.MIN_MAX)
      this.projectCoordinate(t.min, e.min, r), this.projectCoordinate(t.max, e.max, r);
    else if (this.projectBoundingBoxMethod === projectBoundingBoxMethods.FOUR_CORNERS || this.projectBoundingBoxMethod === projectBoundingBoxMethods.FOUR_CORNERS_WITH_EQUATOR) {
      let { x: i, y: s, z: n } = t.min, { x: a, y: o, z: h } = t.max;
      _tempVector3In.set(i, s, n), this.projectCoordinate(_tempVector3In, _tempVector3Out, r), e.expandByPoint(_tempVector3Out), _tempVector3In.set(a, o, h), this.projectCoordinate(_tempVector3In, _tempVector3Out, r), e.expandByPoint(_tempVector3Out), _tempVector3In.set(i, o, 0), this.projectCoordinate(_tempVector3In, _tempVector3Out, r), e.expandByPoint(_tempVector3Out), _tempVector3In.set(a, s, 0), this.projectCoordinate(_tempVector3In, _tempVector3Out, r), e.expandByPoint(_tempVector3Out), this.projectBoundingBoxMethod === projectBoundingBoxMethods.FOUR_CORNERS_WITH_EQUATOR && s < 0 && o > 0 && (_tempVector3In.set(i, 0, 0), this.projectCoordinate(_tempVector3In, _tempVector3Out, r), e.expandByPoint(_tempVector3Out), _tempVector3In.set(a, 0, 0), this.projectCoordinate(_tempVector3In, _tempVector3Out, r), e.expandByPoint(_tempVector3Out));
    }
    return e;
  }
  getGeodeticSurfaceNormal(t, e) {
    return e || (e = new Vector3$1()), e.set(0, 0, 1), e;
  }
  getProjectedSurfaceNormal(t, e) {
    return e || (e = new Vector3$1()), e.set(0, 0, 1), e;
  }
  projectedBoxToGeoBox(t, e, r = true) {
    return e || (e = new Box3()), this.unprojectCoordinate(t.min, e.min, r), this.unprojectCoordinate(t.max, e.max, r), e;
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
const extendUnprojectCoordinate = (t, e, r, i, s = false) => {
  if (Math.abs(t) < i)
    return e;
  const n = t > 0 ? 1 : -1;
  if (s)
    return n * r;
  return n * (r * (1 + (Math.abs(t) - i) / i));
}, extendProjectCoordinate = (t, e, r, i, s = false) => {
  if (Math.abs(t) < r)
    return e;
  const n = t > 0 ? 1 : -1;
  if (s)
    return n * i;
  return n * (i * (1 + (Math.abs(t) - r) / r));
}, D2R = Math.PI / 180, A = 6378137, MAXEXTENT = 20037508, R2D = 180 / Math.PI, MAXLON = 85.0511287798;
function toMercator(t, e = null, r = false) {
  var i = Math.abs(t[0]) <= 180 ? t[0] : t[0] - 360 * sign(t[0]);
  const s = e || [0, 0];
  return s[0] = A * i * D2R, s[1] = A * Math.log(Math.tan(0.25 * Math.PI + 0.5 * t[1] * D2R)), r ? (s[0] = extendProjectCoordinate(t[0], s[0], 180, MAXEXTENT), s[1] = extendProjectCoordinate(t[1], s[1], MAXLON, MAXEXTENT)) : (s[0] > MAXEXTENT && (s[0] = MAXEXTENT), s[0] < -MAXEXTENT && (s[0] = -MAXEXTENT), s[1] > MAXEXTENT && (s[1] = MAXEXTENT), s[1] < -MAXEXTENT && (s[1] = -MAXEXTENT)), s;
}
function toWgs84(t, e, r = false) {
  const i = e || [0, 0];
  return i[0] = t[0] * R2D / A, i[1] = (0.5 * Math.PI - 2 * Math.atan(Math.exp(-t[1] / A))) * R2D, r && (i[0] = extendUnprojectCoordinate(t[0], i[0], 180, MAXEXTENT), i[1] = extendUnprojectCoordinate(t[1], i[1], MAXLON, MAXEXTENT)), i;
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
  projectCoordinate(t, e, r = false) {
    _inputArray$1[0] = t.x, _inputArray$1[1] = t.y, r || (_inputArray$1[0] < -180 && (_inputArray$1[0] = -180), _inputArray$1[0] > 180 && (_inputArray$1[0] = 180), _inputArray$1[1] < -85.0511287798 && (_inputArray$1[1] = -85.0511287798), _inputArray$1[1] > 85.0511287798 && (_inputArray$1[1] = 85.0511287798));
    const i = toMercator(_inputArray$1, _tempCoordinate, r);
    return e || (e = new Vector3$1()), e.x = i[0], e.y = i[1], e.z = t.z, e;
  }
  unprojectCoordinate(t, e, r = false) {
    const i = toWgs84([t.x, t.y], _tempCoordinate, r);
    return e || (e = new Vector3$1()), e.x = i[0], e.y = i[1], e.z = t.z, e;
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
    return defined$2(t) ? (t.copy(this.center), t) : this.center.clone();
  }
  intersectsObb(t) {
    const e = this.center, r = t.center, i = this.halfAxes, s = t.halfAxes, n = new Vector3$1().subVectors(r, e), a = new Vector3$1(i.elements[0], i.elements[1], i.elements[2]), o = new Vector3$1(i.elements[3], i.elements[4], i.elements[5]), h = new Vector3$1(i.elements[6], i.elements[7], i.elements[8]), l = new Vector3$1(s.elements[0], s.elements[1], s.elements[2]), c = new Vector3$1(s.elements[3], s.elements[4], s.elements[5]), u = new Vector3$1(s.elements[6], s.elements[7], s.elements[8]), d = a.length(), p = o.length(), f = h.length();
    a.normalize(), o.normalize(), h.normalize();
    const _ = l.length(), y = c.length(), m = u.length();
    let g, x, M;
    return l.normalize(), c.normalize(), u.normalize(), g = d, x = _ * Math.abs(a.dot(l)) + y * Math.abs(a.dot(c)) + m * Math.abs(a.dot(u)), M = Math.abs(n.dot(a)), !(M > g + x) && (g = p, x = _ * Math.abs(o.dot(l)) + y * Math.abs(o.dot(c)) + m * Math.abs(o.dot(u)), M = Math.abs(n.dot(o)), !(M > g + x) && (g = f, x = _ * Math.abs(h.dot(l)) + y * Math.abs(h.dot(c)) + m * Math.abs(h.dot(u)), M = Math.abs(n.dot(h)), !(M > g + x) && (g = d * Math.abs(l.dot(a)) + p * Math.abs(l.dot(o)) + f * Math.abs(l.dot(h)), x = _, M = Math.abs(n.dot(l)), !(M > g + x) && (g = d * Math.abs(c.dot(a)) + p * Math.abs(c.dot(o)) + f * Math.abs(c.dot(h)), x = y, M = Math.abs(n.dot(c)), !(M > g + x) && (g = d * Math.abs(u.dot(a)) + p * Math.abs(u.dot(o)) + f * Math.abs(u.dot(h)), x = m, M = Math.abs(n.dot(u)), !(M > g + x))))));
  }
}
const scratchOffset = new Vector3$1(), scratchScale = new Vector3$1();
function fromPlaneExtents(t, e, r, i, s, n, a, o, h, l, c) {
  if (!(defined$2(s) && defined$2(n) && defined$2(a) && defined$2(o) && defined$2(h) && defined$2(l)))
    throw new DeveloperError("all extents (minimum/maximum X/Y/Z) are required.");
  defined$2(c) || (c = new OrientedBoundingBox());
  const u = c.halfAxes;
  StaticMatrix3.setColumn(u, 0, e, u), StaticMatrix3.setColumn(u, 1, r, u), StaticMatrix3.setColumn(u, 2, i, u);
  let d = scratchOffset;
  d.x = (s + n) / 2, d.y = (a + o) / 2, d.z = (h + l) / 2;
  const p = scratchScale;
  p.x = (n - s) / 2, p.y = (o - a) / 2, p.z = (l - h) / 2;
  const f = c.center;
  return d = StaticMatrix3.multiplyByVector(u, d, d), Cartesian3.add(t, d, f), StaticMatrix3.multiplyByScale(u, p, u), c;
}
const scratchRectangleCenterCartographic = new Vector3$1(), scratchRectangleCenter = new Vector3$1(), scratchPerimeterCartographicNC = new Vector3$1(), scratchPerimeterCartographicNW = new Vector3$1(), scratchPerimeterCartographicCW = new Vector3$1(), scratchPerimeterCartographicSW = new Vector3$1(), scratchPerimeterCartographicSC = new Vector3$1(), scratchPerimeterCartesianNC = new Vector3$1(), scratchPerimeterCartesianNW = new Vector3$1(), scratchPerimeterCartesianCW = new Vector3$1(), scratchPerimeterCartesianSW = new Vector3$1(), scratchPerimeterCartesianSC = new Vector3$1(), scratchPerimeterProjectedNC = new Vector2(), scratchPerimeterProjectedNW = new Vector2(), scratchPerimeterProjectedCW = new Vector2(), scratchPerimeterProjectedSW = new Vector2(), scratchPerimeterProjectedSC = new Vector2(), scratchPlaneOrigin = new Vector3$1(), scratchPlaneNormal = new Vector3$1(), scratchPlaneXAxis = new Vector3$1(), scratchHorizonCartesian = new Vector3$1(), scratchHorizonProjected = new Vector2(), scratchMaxY = new Vector3$1(), scratchMinY = new Vector3$1(), scratchZ = new Vector3$1(), scratchPlane = new Plane(new Vector3$1(1, 0, 0), 0);
OrientedBoundingBox.fromRectangle = function(t, e, r, i, s) {
  if (!defined$2(t))
    throw new DeveloperError("rectangle is required");
  if (t.width < 0 || t.width > CesiumMath.TWO_PI)
    throw new DeveloperError("Rectangle width must be between 0 and 2 * pi");
  if (t.height < 0 || t.height > CesiumMath.PI)
    throw new DeveloperError("Rectangle height must be between 0 and pi");
  if (defined$2(i) && !CesiumMath.equalsEpsilon(i.radii.x, i.radii.y, CesiumMath.EPSILON15))
    throw new DeveloperError("Ellipsoid must be an ellipsoid of revolution (radii.x == radii.y)");
  let n, a, o, h, l, c, u;
  if (e = defaultValue(e, 0), r = defaultValue(r, 0), i = defaultValue(i, Ellipsoid.WGS84), t.width <= CesiumMath.PI) {
    const d2 = Rectangle.center(t, scratchRectangleCenterCartographic), p2 = i.cartographicToCartesian(d2, scratchRectangleCenter), f2 = new EllipsoidTangentPlane(p2, i);
    u = f2.plane;
    const _2 = d2.x, y2 = t.south < 0 && t.north > 0 ? 0 : d2.y, m2 = Cartographic.fromRadians(_2, t.north, r, scratchPerimeterCartographicNC), g2 = Cartographic.fromRadians(t.west, t.north, r, scratchPerimeterCartographicNW), x2 = Cartographic.fromRadians(t.west, y2, r, scratchPerimeterCartographicCW), M2 = Cartographic.fromRadians(t.west, t.south, r, scratchPerimeterCartographicSW), w2 = Cartographic.fromRadians(_2, t.south, r, scratchPerimeterCartographicSC), b = i.cartographicToCartesian(m2, scratchPerimeterCartesianNC);
    let v = i.cartographicToCartesian(g2, scratchPerimeterCartesianNW);
    const S = i.cartographicToCartesian(x2, scratchPerimeterCartesianCW);
    let C = i.cartographicToCartesian(M2, scratchPerimeterCartesianSW);
    const E = i.cartographicToCartesian(w2, scratchPerimeterCartesianSC), P = f2.projectPointToNearestOnPlane(b, scratchPerimeterProjectedNC), T = f2.projectPointToNearestOnPlane(v, scratchPerimeterProjectedNW), I = f2.projectPointToNearestOnPlane(S, scratchPerimeterProjectedCW), z = f2.projectPointToNearestOnPlane(C, scratchPerimeterProjectedSW), A2 = f2.projectPointToNearestOnPlane(E, scratchPerimeterProjectedSC);
    return n = Math.min(T.x, I.x, z.x), a = -n, h = Math.max(T.y, P.y), o = Math.min(z.y, A2.y), g2.z = M2.z = e, v = i.cartographicToCartesian(g2, scratchPerimeterCartesianNW), C = i.cartographicToCartesian(M2, scratchPerimeterCartesianSW), l = Math.min(StaticPlane.getPointDistance(u, v), StaticPlane.getPointDistance(u, C)), c = r, fromPlaneExtents(f2.origin, f2.xAxis, f2.yAxis, f2.zAxis, n, a, o, h, l, c, s);
  }
  const d = t.south > 0, p = t.north < 0, f = d ? t.south : p ? t.north : 0, _ = Rectangle.center(t, scratchRectangleCenterCartographic).x, y = Cartesian3.fromRadians(_, f, r, i, scratchPlaneOrigin);
  y.z = 0;
  const m = Math.abs(y.x) < CesiumMath.EPSILON10 && Math.abs(y.y) < CesiumMath.EPSILON10 ? Cartesian3.UNIT_X : Cartesian3.normalize(y, scratchPlaneNormal), g = Cartesian3.UNIT_Z, x = Cartesian3.cross(m, g, scratchPlaneXAxis);
  u = StaticPlane.fromPointNormal(y, m, scratchPlane);
  const M = Cartesian3.fromRadians(_ + CesiumMath.PI_OVER_TWO, f, r, i, scratchHorizonCartesian);
  a = Cartesian3.dot(StaticPlane.projectPointOntoPlane(u, M, scratchHorizonProjected), x), n = -a, h = Cartesian3.fromRadians(0, t.north, p ? e : r, i, scratchMaxY).z, o = Cartesian3.fromRadians(0, t.south, d ? e : r, i, scratchMinY).z;
  const w = Cartesian3.fromRadians(t.east, f, r, i, scratchZ);
  return l = StaticPlane.getPointDistance(u, w), c = 0, fromPlaneExtents(y, x, g, m, n, a, o, h, l, c, s);
};
const scratchCartesianU = new Vector3$1(), scratchCartesianV = new Vector3$1(), scratchCartesianW = new Vector3$1(), scratchValidAxis2 = new Vector3$1(), scratchValidAxis3 = new Vector3$1(), scratchPPrime = new Vector3$1();
OrientedBoundingBox.distanceSquaredTo = function(t, e) {
  if (!defined$2(t))
    throw new DeveloperError("box is required.");
  if (!defined$2(e))
    throw new DeveloperError("cartesian is required.");
  const r = Cartesian3.subtract(e, t.center, scratchOffset), i = t.halfAxes;
  let s = StaticMatrix3.getColumn(i, 0, scratchCartesianU), n = StaticMatrix3.getColumn(i, 1, scratchCartesianV), a = StaticMatrix3.getColumn(i, 2, scratchCartesianW);
  const o = Cartesian3.magnitude(s), h = Cartesian3.magnitude(n), l = Cartesian3.magnitude(a);
  let c = true, u = true, d = true;
  o > 0 ? Cartesian3.divideByScalar(s, o, s) : c = false, h > 0 ? Cartesian3.divideByScalar(n, h, n) : u = false, l > 0 ? Cartesian3.divideByScalar(a, l, a) : d = false;
  const p = !c + !u + !d;
  let f, _, y;
  if (1 === p) {
    let t4 = s;
    f = n, _ = a, u ? d || (t4 = a, _ = s) : (t4 = n, f = s), y = Cartesian3.cross(f, _, scratchValidAxis3), t4 === s ? s = y : t4 === n ? n = y : t4 === a && (a = y);
  } else if (2 === p) {
    f = s, u ? f = n : d && (f = a);
    let t4 = Cartesian3.UNIT_Y;
    Cartesian3.equalsEpsilon(t4, f, CesiumMath.EPSILON3) && (t4 = Cartesian3.UNIT_X), _ = Cartesian3.cross(f, t4, scratchValidAxis2), Cartesian3.normalize(_, _), y = Cartesian3.cross(f, _, scratchValidAxis3), Cartesian3.normalize(y, y), f === s ? (n = _, a = y) : f === n ? (a = _, s = y) : f === a && (s = _, n = y);
  } else
    3 === p && (s = Cartesian3.UNIT_X, n = Cartesian3.UNIT_Y, a = Cartesian3.UNIT_Z);
  const m = scratchPPrime;
  m.x = Cartesian3.dot(r, s), m.y = Cartesian3.dot(r, n), m.z = Cartesian3.dot(r, a);
  let g, x = 0;
  return m.x < -o ? (g = m.x + o, x += g * g) : m.x > o && (g = m.x - o, x += g * g), m.y < -h ? (g = m.y + h, x += g * g) : m.y > h && (g = m.y - h, x += g * g), m.z < -l ? (g = m.z + l, x += g * g) : m.z > l && (g = m.z - l, x += g * g), x;
}, OrientedBoundingBox.intersectPlane = function(t, e) {
  if (!defined$2(t))
    throw new DeveloperError("box is required.");
  if (!defined$2(e))
    throw new DeveloperError("plane is required.");
  const r = t.center, i = e.normal, s = t.halfAxes, n = i.x, a = i.y, o = i.z, h = s.elements, l = Math.abs(n * h[StaticMatrix3.COLUMN0ROW0] + a * h[StaticMatrix3.COLUMN0ROW1] + o * h[StaticMatrix3.COLUMN0ROW2]) + Math.abs(n * h[StaticMatrix3.COLUMN1ROW0] + a * h[StaticMatrix3.COLUMN1ROW1] + o * h[StaticMatrix3.COLUMN1ROW2]) + Math.abs(n * h[StaticMatrix3.COLUMN2ROW0] + a * h[StaticMatrix3.COLUMN2ROW1] + o * h[StaticMatrix3.COLUMN2ROW2]), c = Cartesian3.dot(i.clone(), r) + e.constant;
  return c <= -l ? Intersect$1.OUTSIDE : c >= l ? Intersect$1.INSIDE : Intersect$1.INTERSECTING;
};
const scratchXAxis = new Vector3$1(), scratchYAxis = new Vector3$1(), scratchZAxis = new Vector3$1();
OrientedBoundingBox.computeCorners = function(t, e) {
  defined$2(e) || (e = [new Vector3$1(), new Vector3$1(), new Vector3$1(), new Vector3$1(), new Vector3$1(), new Vector3$1(), new Vector3$1(), new Vector3$1()]);
  const r = t.center, i = t.halfAxes, s = StaticMatrix3.getColumn(i, 0, scratchXAxis), n = StaticMatrix3.getColumn(i, 1, scratchYAxis), a = StaticMatrix3.getColumn(i, 2, scratchZAxis);
  return Cartesian3.clone(r, e[0]), Cartesian3.subtract(e[0], s, e[0]), Cartesian3.subtract(e[0], n, e[0]), Cartesian3.subtract(e[0], a, e[0]), Cartesian3.clone(r, e[1]), Cartesian3.subtract(e[1], s, e[1]), Cartesian3.subtract(e[1], n, e[1]), Cartesian3.add(e[1], a, e[1]), Cartesian3.clone(r, e[2]), Cartesian3.subtract(e[2], s, e[2]), Cartesian3.add(e[2], n, e[2]), Cartesian3.subtract(e[2], a, e[2]), Cartesian3.clone(r, e[3]), Cartesian3.subtract(e[3], s, e[3]), Cartesian3.add(e[3], n, e[3]), Cartesian3.add(e[3], a, e[3]), Cartesian3.clone(r, e[4]), Cartesian3.add(e[4], s, e[4]), Cartesian3.subtract(e[4], n, e[4]), Cartesian3.subtract(e[4], a, e[4]), Cartesian3.clone(r, e[5]), Cartesian3.add(e[5], s, e[5]), Cartesian3.subtract(e[5], n, e[5]), Cartesian3.add(e[5], a, e[5]), Cartesian3.clone(r, e[6]), Cartesian3.add(e[6], s, e[6]), Cartesian3.add(e[6], n, e[6]), Cartesian3.subtract(e[6], a, e[6]), Cartesian3.clone(r, e[7]), Cartesian3.add(e[7], s, e[7]), Cartesian3.add(e[7], n, e[7]), Cartesian3.add(e[7], a, e[7]), e;
}, OrientedBoundingBox.fromGeoBoundingBox = function(t, e) {
  if (!defined$2(t))
    throw new DeveloperError("geoBoundingBox is required.");
  const r = t.min, i = t.max, s = Rectangle.fromBox(t, null, true);
  return OrientedBoundingBox.fromRectangle(s, r.z, i.z, null, e);
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
  for (let r in e)
    t[r] = e[r];
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
  let e = this.lng, r = Math.abs(t - e);
  return r > 180 && (r = 360 - r), r;
}, sub: function(t) {
  return new Point(this.lat - t.lat, this.lng - t.lng);
}, toString: function() {
  return "Point";
} }), extend(MercatorProjection, { EARTHRADIUS: 637099681e-2, MCBAND: [1289059486e-2, 836237787e-2, 5591021, 348198983e-2, 167804312e-2, 0], LLBAND: [75, 60, 45, 30, 15, 0], MC2LL: [[1410526172116255e-23, 898305509648872e-20, -1.9939833816331, 200.9824383106796, -187.2403703815547, 91.6087516669843, -23.38765649603339, 2.57121317296198, -0.03801003308653, 173379812e-1], [-7435856389565537e-24, 8983055097726239e-21, -0.78625201886289, 96.32687599759846, -1.85204757529826, -59.36935905485877, 47.40033549296737, -16.50741931063887, 2.28786674699375, 1026014486e-2], [-3030883460898826e-23, 898305509983578e-20, 0.30071316287616, 59.74293618442277, 7.357984074871, -25.38371002664745, 13.45380521110908, -3.29883767235584, 0.32710905363475, 685681737e-2], [-1981981304930552e-23, 8983055099779535e-21, 0.03278182852591, 40.31678527705744, 0.65659298677277, -4.44255534477492, 0.85341911805263, 0.12923347998204, -0.04625736007561, 448277706e-2], [309191371068437e-23, 8983055096812155e-21, 6995724062e-14, 23.10934304144901, -23663490511e-14, -0.6321817810242, -0.00663494467273, 0.03430082397953, -0.00466043876332, 25551644e-1], [2890871144776878e-24, 8983055095805407e-21, -3068298e-14, 7.47137025468032, -353937994e-14, -0.02145144861037, -1234426596e-14, 10322952773e-14, -323890364e-14, 826088.5]], LL2MC: [[-0.0015702102444, 111320.7020616939, 1704480524535203, -10338987376042340, 26112667856603880, -35149669176653700, 26595700718403920, -10725012454188240, 1800819912950474, 82.5], [8277824516172526e-19, 111320.7020463578, 6477955746671607e-7, -4082003173641316e-6, 1077490566351142e-5, -1517187553151559e-5, 1205306533862167e-5, -5124939663577472e-6, 9133119359512032e-7, 67.5], [0.00337398766765, 111320.7020202162, 4481351045890365e-9, -2339375119931662e-8, 7968221547186455e-8, -1159649932797253e-7, 9723671115602145e-8, -4366194633752821e-8, 8477230501135234e-9, 52.5], [0.00220636496208, 111320.7020209128, 51751.86112841131, 3796837749470245e-9, 992013.7397791013, -122195221711287e-8, 1340652697009075e-9, -620943.6990984312, 144416.9293806241, 37.5], [-3441963504368392e-19, 111320.7020576856, 278.2353980772752, 2485758690035394e-9, 6070.750963243378, 54821.18345352118, 9540.606633304236, -2710.55326746645, 1405.483844121726, 22.5], [-3218135878613132e-19, 111320.7020701615, 0.00369383431289, 823725.6402795718, 0.46104986909093, 2351.343141331292, 1.58060784298199, 8.77738589078284, 0.37238884252424, 7.45]], getDistanceByMC: function(t, e) {
  if (!t || !e)
    return 0;
  let r, i, s, n;
  return (t = this.convertMC2LL(t)) ? (r = this.toRadians(t.lng), i = this.toRadians(t.lat), (e = this.convertMC2LL(e)) ? (s = this.toRadians(e.lng), n = this.toRadians(e.lat), this.getDistance(r, s, i, n)) : 0) : 0;
}, getDistanceByLL: function(t, e) {
  if (!t || !e)
    return 0;
  let r, i, s, n;
  return t.lng = this.getLoop(t.lng, -180, 180), t.lat = this.getRange(t.lat, -74, 74), e.lng = this.getLoop(e.lng, -180, 180), e.lat = this.getRange(e.lat, -74, 74), r = this.toRadians(t.lng), s = this.toRadians(t.lat), i = this.toRadians(e.lng), n = this.toRadians(e.lat), this.getDistance(r, i, s, n);
}, convertMC2LL: function(t) {
  if (null == t)
    return new Point(0, 0);
  if (t.lng < 180 && t.lng > -180 && t.lat < 90 && t.lat > -90)
    return t;
  let e, r;
  e = new Point(Math.abs(t.lng), Math.abs(t.lat));
  for (let t4 = 0; t4 < this.MCBAND.length; t4++)
    if (e.lat >= this.MCBAND[t4]) {
      r = this.MC2LL[t4];
      break;
    }
  let i = this.convertor(t, r);
  return t = new Point(i.lng.toFixed(6), i.lat.toFixed(6));
}, convertLL2MC: function(t) {
  if (null == t)
    return new Point(0, 0);
  if (t.lng > 180 || t.lng < -180 || t.lat > 90 || t.lat < -90)
    return t;
  let e, r;
  t.lng = this.getLoop(t.lng, -180, 180), t.lat = this.getRange(t.lat, -74, 74), e = new Point(t.lng, t.lat);
  for (var i = 0; i < this.LLBAND.length; i++)
    if (e.lat >= this.LLBAND[i]) {
      r = this.LL2MC[i];
      break;
    }
  if (!r) {
    for (i = 0; i < this.LLBAND.length; i++)
      if (e.lat <= -this.LLBAND[i]) {
        r = this.LL2MC[i];
        break;
      }
  }
  let s = this.convertor(t, r);
  return t = new Point(Number(s.lng), Number(s.lat));
}, convertor: function(t, e) {
  if (!t || !e)
    return;
  let r = e[0] + e[1] * Math.abs(t.lng), i = Math.abs(t.lat) / e[9], s = e[2] + e[3] * i + e[4] * i * i + e[5] * i * i * i + e[6] * i * i * i * i + e[7] * i * i * i * i * i + e[8] * i * i * i * i * i * i;
  return r *= t.lng < 0 ? -1 : 1, s *= t.lat < 0 ? -1 : 1, new Point(r, s);
}, getDistance: function(t, e, r, i) {
  return this.EARTHRADIUS * Math.acos(Math.sin(r) * Math.sin(i) + Math.cos(r) * Math.cos(i) * Math.cos(e - t));
}, toRadians: function(t) {
  return Math.PI * t / 180;
}, toDegrees: function(t) {
  return 180 * t / Math.PI;
}, getRange: function(t, e, r) {
  return null != e && (t = Math.max(t, e)), null != r && (t = Math.min(t, r)), t;
}, getLoop: function(t, e, r) {
  for (; t > r; )
    t -= r - e;
  for (; t < e; )
    t += r - e;
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
}, pointToPixel: function(t, e, r, i, s) {
  if (!t)
    return;
  t = this.lngLatToMercator(t, s);
  let n = this.getZoomUnits(e);
  return new Pixel(Math.round((t.lng - r.lng) / n + i.width / 2), Math.round((r.lat - t.lat) / n + i.height / 2));
}, pixelToPoint: function(t, e, r, i, s) {
  if (!t)
    return;
  let n = this.getZoomUnits(e), a = new Point(r.lng + n * (t.x - i.width / 2), r.lat - n * (t.y - i.height / 2));
  return this.mercatorToLngLat(a, s);
}, getZoomUnits: function(t) {
  return Math.pow(2, 18 - t);
} });
const MAX_X = 2003772636e-2, MAX_Y = 1247410417e-2, MAX_LAT = 74, MIN_LAT = -MAX_LAT, _tempInput = new Vector3$1();
class BaiduMercatorProjection extends Projection {
  constructor() {
    super(...arguments);
    __publicField(this, "name", PROJECTION_BD_MERCATOR);
    __publicField(this, "isAxisAligned", true);
    __publicField(this, "unprojectCoordinate", (t, e, r) => {
      e || (e = new Vector3$1()), _tempInput.copy(t), t.x < -MAX_X && (_tempInput.x = -MAX_X), t.x > MAX_X && (_tempInput.x = MAX_X), t.y < -MAX_Y && (_tempInput.y = -MAX_Y), t.y > MAX_Y && (_tempInput.y = MAX_Y);
      const i = MercatorProjection.convertMC2LL({ lng: _tempInput.x, lat: _tempInput.y });
      return e.set(Number(i.lng), Number(i.lat), _tempInput.z), r && (e.x = extendUnprojectCoordinate(t.x, e.x, 180, MAX_X), e.y = extendUnprojectCoordinate(t.y, e.y, MAX_LAT, MAX_Y)), e;
    });
  }
  projectCoordinate(t, e, r = false) {
    e || (e = new Vector3$1()), _tempInput.copy(t), t.x < -180 && (_tempInput.x = -180), t.x > 180 && (_tempInput.x = 180), t.y < MIN_LAT && (_tempInput.y = MIN_LAT), t.y > MAX_LAT && (_tempInput.y = MAX_LAT);
    const i = MercatorProjection.convertLL2MC({ lng: _tempInput.x, lat: _tempInput.y });
    return e.set(Number(i.lng), Number(i.lat), _tempInput.z), r && (e.x = extendProjectCoordinate(t.x, e.x, 180, MAX_X), e.y = extendProjectCoordinate(t.y, e.y, MAX_LAT, MAX_Y)), e;
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
  function t(t4) {
    t4("EPSG:4326", "+title=WGS 84 (long/lat) +proj=longlat +ellps=WGS84 +datum=WGS84 +units=degrees"), t4("EPSG:4269", "+title=NAD83 (long/lat) +proj=longlat +a=6378137.0 +b=6356752.31414036 +ellps=GRS80 +datum=NAD83 +units=degrees"), t4("EPSG:3857", "+title=WGS 84 / Pseudo-Mercator +proj=merc +a=6378137 +b=6378137 +lat_ts=0.0 +lon_0=0.0 +x_0=0.0 +y_0=0 +k=1.0 +units=m +nadgrids=@null +no_defs");
    for (var e2 = 1; e2 <= 60; ++e2)
      t4("EPSG:" + (32600 + e2), "+proj=utm +zone=" + e2 + " +datum=WGS84 +units=m"), t4("EPSG:" + (32700 + e2), "+proj=utm +zone=" + e2 + " +south +datum=WGS84 +units=m");
    t4.WGS84 = t4["EPSG:4326"], t4["EPSG:3785"] = t4["EPSG:3857"], t4.GOOGLE = t4["EPSG:3857"], t4["EPSG:900913"] = t4["EPSG:3857"], t4["EPSG:102113"] = t4["EPSG:3857"];
  }
  var e = 1, r = 2, i = 3, s = 4, n = 5, a = 6378137, o = 6356752314e-3, h = 0.0066943799901413165, l = 484813681109536e-20, c = Math.PI / 2, u = 0.16666666666666666, d = 0.04722222222222222, p = 0.022156084656084655, f = 1e-10, _ = 0.017453292519943295, y = 57.29577951308232, m = Math.PI / 4, g = 2 * Math.PI, x = 3.14159265359, M = { greenwich: 0, lisbon: -9.131906111111, paris: 2.337229166667, bogota: -74.080916666667, madrid: -3.687938888889, rome: 12.452333333333, bern: 7.439583333333, jakarta: 106.807719444444, ferro: -17.666666666667, brussels: 4.367975, stockholm: 18.058277777778, athens: 23.7163375, oslo: 10.722916666667 }, w = { mm: { to_meter: 1e-3 }, cm: { to_meter: 0.01 }, ft: { to_meter: 0.3048 }, "us-ft": { to_meter: 1200 / 3937 }, fath: { to_meter: 1.8288 }, kmi: { to_meter: 1852 }, "us-ch": { to_meter: 20.1168402336805 }, "us-mi": { to_meter: 1609.34721869444 }, km: { to_meter: 1e3 }, "ind-ft": { to_meter: 0.30479841 }, "ind-yd": { to_meter: 0.91439523 }, mi: { to_meter: 1609.344 }, yd: { to_meter: 0.9144 }, ch: { to_meter: 20.1168 }, link: { to_meter: 0.201168 }, dm: { to_meter: 0.1 }, in: { to_meter: 0.0254 }, "ind-ch": { to_meter: 20.11669506 }, "us-in": { to_meter: 0.025400050800101 }, "us-yd": { to_meter: 0.914401828803658 } }, b = /[\s_\-\/\(\)]/g;
  function v(t4, e2) {
    if (t4[e2])
      return t4[e2];
    for (var r2, i2 = Object.keys(t4), s2 = e2.toLowerCase().replace(b, ""), n2 = -1; ++n2 < i2.length; )
      if ((r2 = i2[n2]).toLowerCase().replace(b, "") === s2)
        return t4[r2];
  }
  function S(t4) {
    var e2, r2, i2, s2 = {}, n2 = t4.split("+").map(function(t5) {
      return t5.trim();
    }).filter(function(t5) {
      return t5;
    }).reduce(function(t5, e3) {
      var r3 = e3.split("=");
      return r3.push(true), t5[r3[0].toLowerCase()] = r3[1], t5;
    }, {}), a2 = { proj: "projName", datum: "datumCode", rf: function(t5) {
      s2.rf = parseFloat(t5);
    }, lat_0: function(t5) {
      s2.lat0 = t5 * _;
    }, lat_1: function(t5) {
      s2.lat1 = t5 * _;
    }, lat_2: function(t5) {
      s2.lat2 = t5 * _;
    }, lat_ts: function(t5) {
      s2.lat_ts = t5 * _;
    }, lon_0: function(t5) {
      s2.long0 = t5 * _;
    }, lon_1: function(t5) {
      s2.long1 = t5 * _;
    }, lon_2: function(t5) {
      s2.long2 = t5 * _;
    }, alpha: function(t5) {
      s2.alpha = parseFloat(t5) * _;
    }, gamma: function(t5) {
      s2.rectified_grid_angle = parseFloat(t5) * _;
    }, lonc: function(t5) {
      s2.longc = t5 * _;
    }, x_0: function(t5) {
      s2.x0 = parseFloat(t5);
    }, y_0: function(t5) {
      s2.y0 = parseFloat(t5);
    }, k_0: function(t5) {
      s2.k0 = parseFloat(t5);
    }, k: function(t5) {
      s2.k0 = parseFloat(t5);
    }, a: function(t5) {
      s2.a = parseFloat(t5);
    }, b: function(t5) {
      s2.b = parseFloat(t5);
    }, r: function(t5) {
      s2.a = s2.b = parseFloat(t5);
    }, r_a: function() {
      s2.R_A = true;
    }, zone: function(t5) {
      s2.zone = parseInt(t5, 10);
    }, south: function() {
      s2.utmSouth = true;
    }, towgs84: function(t5) {
      s2.datum_params = t5.split(",").map(function(t6) {
        return parseFloat(t6);
      });
    }, to_meter: function(t5) {
      s2.to_meter = parseFloat(t5);
    }, units: function(t5) {
      s2.units = t5;
      var e3 = v(w, t5);
      e3 && (s2.to_meter = e3.to_meter);
    }, from_greenwich: function(t5) {
      s2.from_greenwich = t5 * _;
    }, pm: function(t5) {
      var e3 = v(M, t5);
      s2.from_greenwich = (e3 || parseFloat(t5)) * _;
    }, nadgrids: function(t5) {
      "@null" === t5 ? s2.datumCode = "none" : s2.nadgrids = t5;
    }, axis: function(t5) {
      var e3 = "ewnsud";
      3 === t5.length && -1 !== e3.indexOf(t5.substr(0, 1)) && -1 !== e3.indexOf(t5.substr(1, 1)) && -1 !== e3.indexOf(t5.substr(2, 1)) && (s2.axis = t5);
    }, approx: function() {
      s2.approx = true;
    } };
    for (e2 in n2)
      r2 = n2[e2], e2 in a2 ? "function" == typeof (i2 = a2[e2]) ? i2(r2) : s2[i2] = r2 : s2[e2] = r2;
    return "string" == typeof s2.datumCode && "WGS84" !== s2.datumCode && (s2.datumCode = s2.datumCode.toLowerCase()), s2;
  }
  class C {
    static getId(t4) {
      const e2 = t4.find((t5) => Array.isArray(t5) && "ID" === t5[0]);
      return e2 && e2.length >= 3 ? { authority: e2[1], code: parseInt(e2[2], 10) } : null;
    }
    static convertUnit(t4, e2 = "unit") {
      if (!t4 || t4.length < 3)
        return { type: e2, name: "unknown", conversion_factor: null };
      const r2 = t4[1], i2 = parseFloat(t4[2]) || null, s2 = t4.find((t5) => Array.isArray(t5) && "ID" === t5[0]);
      return { type: e2, name: r2, conversion_factor: i2, id: s2 ? { authority: s2[1], code: parseInt(s2[2], 10) } : null };
    }
    static convertAxis(t4) {
      var _a;
      const e2 = t4[1] || "Unknown";
      let r2;
      const i2 = e2.match(/^\((.)\)$/);
      if (i2) {
        const t5 = i2[1].toUpperCase();
        if ("E" === t5)
          r2 = "east";
        else if ("N" === t5)
          r2 = "north";
        else {
          if ("U" !== t5)
            throw new Error(`Unknown axis abbreviation: ${t5}`);
          r2 = "up";
        }
      } else
        r2 = ((_a = t4[2]) == null ? void 0 : _a.toLowerCase()) || "unknown";
      const s2 = t4.find((t5) => Array.isArray(t5) && "ORDER" === t5[0]), n2 = s2 ? parseInt(s2[1], 10) : null, a2 = t4.find((t5) => Array.isArray(t5) && ("LENGTHUNIT" === t5[0] || "ANGLEUNIT" === t5[0] || "SCALEUNIT" === t5[0]));
      return { name: e2, direction: r2, unit: this.convertUnit(a2), order: n2 };
    }
    static extractAxes(t4) {
      return t4.filter((t5) => Array.isArray(t5) && "AXIS" === t5[0]).map((t5) => this.convertAxis(t5)).sort((t5, e2) => (t5.order || 0) - (e2.order || 0));
    }
    static convert(t4, e2 = {}) {
      switch (t4[0]) {
        case "PROJCRS":
          e2.type = "ProjectedCRS", e2.name = t4[1], e2.base_crs = t4.find((t5) => Array.isArray(t5) && "BASEGEOGCRS" === t5[0]) ? this.convert(t4.find((t5) => Array.isArray(t5) && "BASEGEOGCRS" === t5[0])) : null, e2.conversion = t4.find((t5) => Array.isArray(t5) && "CONVERSION" === t5[0]) ? this.convert(t4.find((t5) => Array.isArray(t5) && "CONVERSION" === t5[0])) : null;
          const r2 = t4.find((t5) => Array.isArray(t5) && "CS" === t5[0]);
          r2 && (e2.coordinate_system = { type: r2[1], axis: this.extractAxes(t4) });
          const i2 = t4.find((t5) => Array.isArray(t5) && "LENGTHUNIT" === t5[0]);
          if (i2) {
            const t5 = this.convertUnit(i2);
            e2.coordinate_system.unit = t5;
          }
          e2.id = this.getId(t4);
          break;
        case "BASEGEOGCRS":
        case "GEOGCRS":
          e2.type = "GeographicCRS", e2.name = t4[1];
          const s2 = t4.find((t5) => Array.isArray(t5) && ("DATUM" === t5[0] || "ENSEMBLE" === t5[0]));
          if (s2) {
            const r3 = this.convert(s2);
            "ENSEMBLE" === s2[0] ? e2.datum_ensemble = r3 : e2.datum = r3;
            const i3 = t4.find((t5) => Array.isArray(t5) && "PRIMEM" === t5[0]);
            i3 && "Greenwich" !== i3[1] && (r3.prime_meridian = { name: i3[1], longitude: parseFloat(i3[2]) });
          }
          e2.coordinate_system = { type: "ellipsoidal", axis: this.extractAxes(t4) }, e2.id = this.getId(t4);
          break;
        case "DATUM":
          e2.type = "GeodeticReferenceFrame", e2.name = t4[1], e2.ellipsoid = t4.find((t5) => Array.isArray(t5) && "ELLIPSOID" === t5[0]) ? this.convert(t4.find((t5) => Array.isArray(t5) && "ELLIPSOID" === t5[0])) : null;
          break;
        case "ENSEMBLE":
          e2.type = "DatumEnsemble", e2.name = t4[1], e2.members = t4.filter((t5) => Array.isArray(t5) && "MEMBER" === t5[0]).map((t5) => ({ type: "DatumEnsembleMember", name: t5[1], id: this.getId(t5) }));
          const n2 = t4.find((t5) => Array.isArray(t5) && "ENSEMBLEACCURACY" === t5[0]);
          n2 && (e2.accuracy = parseFloat(n2[1]));
          const a2 = t4.find((t5) => Array.isArray(t5) && "ELLIPSOID" === t5[0]);
          a2 && (e2.ellipsoid = this.convert(a2)), e2.id = this.getId(t4);
          break;
        case "ELLIPSOID":
          e2.type = "Ellipsoid", e2.name = t4[1], e2.semi_major_axis = parseFloat(t4[2]), e2.inverse_flattening = parseFloat(t4[3]), t4.find((t5) => Array.isArray(t5) && "LENGTHUNIT" === t5[0]) && this.convert(t4.find((t5) => Array.isArray(t5) && "LENGTHUNIT" === t5[0]), e2);
          break;
        case "CONVERSION":
          e2.type = "Conversion", e2.name = t4[1], e2.method = t4.find((t5) => Array.isArray(t5) && "METHOD" === t5[0]) ? this.convert(t4.find((t5) => Array.isArray(t5) && "METHOD" === t5[0])) : null, e2.parameters = t4.filter((t5) => Array.isArray(t5) && "PARAMETER" === t5[0]).map((t5) => this.convert(t5));
          break;
        case "METHOD":
          e2.type = "Method", e2.name = t4[1], e2.id = this.getId(t4);
          break;
        case "PARAMETER":
          e2.type = "Parameter", e2.name = t4[1], e2.value = parseFloat(t4[2]), e2.unit = this.convertUnit(t4.find((t5) => Array.isArray(t5) && ("LENGTHUNIT" === t5[0] || "ANGLEUNIT" === t5[0] || "SCALEUNIT" === t5[0]))), e2.id = this.getId(t4);
          break;
        case "BOUNDCRS":
          e2.type = "BoundCRS";
          const o2 = t4.find((t5) => Array.isArray(t5) && "SOURCECRS" === t5[0]);
          if (o2) {
            const t5 = o2.find((t6) => Array.isArray(t6));
            e2.source_crs = t5 ? this.convert(t5) : null;
          }
          const h2 = t4.find((t5) => Array.isArray(t5) && "TARGETCRS" === t5[0]);
          if (h2) {
            const t5 = h2.find((t6) => Array.isArray(t6));
            e2.target_crs = t5 ? this.convert(t5) : null;
          }
          const l2 = t4.find((t5) => Array.isArray(t5) && "ABRIDGEDTRANSFORMATION" === t5[0]);
          e2.transformation = l2 ? this.convert(l2) : null;
          break;
        case "ABRIDGEDTRANSFORMATION":
          if (e2.type = "Transformation", e2.name = t4[1], e2.method = t4.find((t5) => Array.isArray(t5) && "METHOD" === t5[0]) ? this.convert(t4.find((t5) => Array.isArray(t5) && "METHOD" === t5[0])) : null, e2.parameters = t4.filter((t5) => Array.isArray(t5) && ("PARAMETER" === t5[0] || "PARAMETERFILE" === t5[0])).map((t5) => "PARAMETER" === t5[0] ? this.convert(t5) : "PARAMETERFILE" === t5[0] ? { name: t5[1], value: t5[2], id: { authority: "EPSG", code: 8656 } } : void 0), 7 === e2.parameters.length) {
            const t5 = e2.parameters[6];
            "Scale difference" === t5.name && (t5.value = Math.round(1e12 * (t5.value - 1)) / 1e6);
          }
          e2.id = this.getId(t4);
          break;
        case "AXIS":
          e2.coordinate_system || (e2.coordinate_system = { type: "unspecified", axis: [] }), e2.coordinate_system.axis.push(this.convertAxis(t4));
          break;
        case "LENGTHUNIT":
          const c2 = this.convertUnit(t4, "LinearUnit");
          e2.coordinate_system && e2.coordinate_system.axis && e2.coordinate_system.axis.forEach((t5) => {
            t5.unit || (t5.unit = c2);
          }), c2.conversion_factor && 1 !== c2.conversion_factor && e2.semi_major_axis && (e2.semi_major_axis = { value: e2.semi_major_axis, unit: c2 });
          break;
        default:
          e2.keyword = t4[0];
      }
      return e2;
    }
  }
  class E extends C {
    static convert(t4, e2 = {}) {
      var _a;
      return super.convert(t4, e2), "Cartesian" === ((_a = e2.coordinate_system) == null ? void 0 : _a.subtype) && delete e2.coordinate_system, e2.usage && delete e2.usage, e2;
    }
  }
  class P extends C {
    static convert(t4, e2 = {}) {
      var _a, _b, _c;
      super.convert(t4, e2);
      const r2 = t4.find((t5) => Array.isArray(t5) && "CS" === t5[0]);
      r2 && (e2.coordinate_system = { subtype: r2[1], axis: this.extractAxes(t4) });
      const i2 = t4.find((t5) => Array.isArray(t5) && "USAGE" === t5[0]);
      return i2 && (e2.usage = { scope: (_a = i2.find((t5) => Array.isArray(t5) && "SCOPE" === t5[0])) == null ? void 0 : _a[1], area: (_b = i2.find((t5) => Array.isArray(t5) && "AREA" === t5[0])) == null ? void 0 : _b[1], bbox: (_c = i2.find((t5) => Array.isArray(t5) && "BBOX" === t5[0])) == null ? void 0 : _c.slice(1) }), e2;
    }
  }
  function T(t4) {
    return t4.find((t5) => Array.isArray(t5) && "USAGE" === t5[0]) ? "2019" : (t4.find((t5) => Array.isArray(t5) && "CS" === t5[0]) || "BOUNDCRS" === t4[0] || "PROJCRS" === t4[0] || t4[0], "2015");
  }
  function I(t4) {
    return ("2019" === T(t4) ? P : E).convert(t4);
  }
  function z(t4) {
    const e2 = t4.toUpperCase();
    return e2.includes("PROJCRS") || e2.includes("GEOGCRS") || e2.includes("BOUNDCRS") || e2.includes("VERTCRS") || e2.includes("LENGTHUNIT") || e2.includes("ANGLEUNIT") || e2.includes("SCALEUNIT") ? "WKT2" : (e2.includes("PROJCS") || e2.includes("GEOGCS") || e2.includes("LOCAL_CS") || e2.includes("VERT_CS") || e2.includes("UNIT"), "WKT1");
  }
  var A2 = 1, O = 2, N = 3, R = 4, $ = 5, V = -1, G = /\s/, k = /[A-Za-z]/, q = /[A-Za-z84_]/, B = /[,\]]/, F = /[\d\.E\-\+]/;
  function L(t4) {
    if ("string" != typeof t4)
      throw new Error("not a string");
    this.text = t4.trim(), this.level = 0, this.place = 0, this.root = null, this.stack = [], this.currentObject = null, this.state = A2;
  }
  function j(t4) {
    return new L(t4).output();
  }
  function D(t4, e2, r2) {
    Array.isArray(e2) && (r2.unshift(e2), e2 = null);
    var i2 = e2 ? {} : t4, s2 = r2.reduce(function(t5, e3) {
      return U(e3, t5), t5;
    }, i2);
    e2 && (t4[e2] = s2);
  }
  function U(t4, e2) {
    if (Array.isArray(t4)) {
      var r2 = t4.shift();
      if ("PARAMETER" === r2 && (r2 = t4.shift()), 1 === t4.length)
        return Array.isArray(t4[0]) ? (e2[r2] = {}, void U(t4[0], e2[r2])) : void (e2[r2] = t4[0]);
      if (t4.length)
        if ("TOWGS84" !== r2) {
          if ("AXIS" === r2)
            return r2 in e2 || (e2[r2] = []), void e2[r2].push(t4);
          var i2;
          switch (Array.isArray(r2) || (e2[r2] = {}), r2) {
            case "UNIT":
            case "PRIMEM":
            case "VERT_DATUM":
              return e2[r2] = { name: t4[0].toLowerCase(), convert: t4[1] }, void (3 === t4.length && U(t4[2], e2[r2]));
            case "SPHEROID":
            case "ELLIPSOID":
              return e2[r2] = { name: t4[0], a: t4[1], rf: t4[2] }, void (4 === t4.length && U(t4[3], e2[r2]));
            case "EDATUM":
            case "ENGINEERINGDATUM":
            case "LOCAL_DATUM":
            case "DATUM":
            case "VERT_CS":
            case "VERTCRS":
            case "VERTICALCRS":
              return t4[0] = ["name", t4[0]], void D(e2, r2, t4);
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
              return t4[0] = ["name", t4[0]], D(e2, r2, t4), void (e2[r2].type = r2);
            default:
              for (i2 = -1; ++i2 < t4.length; )
                if (!Array.isArray(t4[i2]))
                  return U(t4, e2[r2]);
              return D(e2, r2, t4);
          }
        } else
          e2[r2] = t4;
      else
        e2[r2] = true;
    } else
      e2[t4] = true;
  }
  L.prototype.readCharicter = function() {
    var t4 = this.text[this.place++];
    if (this.state !== R)
      for (; G.test(t4); ) {
        if (this.place >= this.text.length)
          return;
        t4 = this.text[this.place++];
      }
    switch (this.state) {
      case A2:
        return this.neutral(t4);
      case O:
        return this.keyword(t4);
      case R:
        return this.quoted(t4);
      case $:
        return this.afterquote(t4);
      case N:
        return this.number(t4);
      case V:
        return;
    }
  }, L.prototype.afterquote = function(t4) {
    if ('"' === t4)
      return this.word += '"', void (this.state = R);
    if (B.test(t4))
      return this.word = this.word.trim(), void this.afterItem(t4);
    throw new Error(`havn't handled "` + t4 + '" in afterquote yet, index ' + this.place);
  }, L.prototype.afterItem = function(t4) {
    return "," === t4 ? (null !== this.word && this.currentObject.push(this.word), this.word = null, void (this.state = A2)) : "]" === t4 ? (this.level--, null !== this.word && (this.currentObject.push(this.word), this.word = null), this.state = A2, this.currentObject = this.stack.pop(), void (this.currentObject || (this.state = V))) : void 0;
  }, L.prototype.number = function(t4) {
    if (!F.test(t4)) {
      if (B.test(t4))
        return this.word = parseFloat(this.word), void this.afterItem(t4);
      throw new Error(`havn't handled "` + t4 + '" in number yet, index ' + this.place);
    }
    this.word += t4;
  }, L.prototype.quoted = function(t4) {
    '"' !== t4 ? this.word += t4 : this.state = $;
  }, L.prototype.keyword = function(t4) {
    if (q.test(t4))
      this.word += t4;
    else {
      if ("[" === t4) {
        var e2 = [];
        return e2.push(this.word), this.level++, null === this.root ? this.root = e2 : this.currentObject.push(e2), this.stack.push(this.currentObject), this.currentObject = e2, void (this.state = A2);
      }
      if (!B.test(t4))
        throw new Error(`havn't handled "` + t4 + '" in keyword yet, index ' + this.place);
      this.afterItem(t4);
    }
  }, L.prototype.neutral = function(t4) {
    if (k.test(t4))
      return this.word = t4, void (this.state = O);
    if ('"' === t4)
      return this.word = "", void (this.state = R);
    if (F.test(t4))
      return this.word = t4, void (this.state = N);
    if (!B.test(t4))
      throw new Error(`havn't handled "` + t4 + '" in neutral yet, index ' + this.place);
    this.afterItem(t4);
  }, L.prototype.output = function() {
    for (; this.place < this.text.length; )
      this.readCharicter();
    if (this.state === V)
      return this.root;
    throw new Error('unable to parse string "' + this.text + '". State is ' + this.state);
  };
  var W = 0.017453292519943295;
  function H(t4) {
    return t4 * W;
  }
  function X(t4) {
    const e2 = (t4.projName || "").toLowerCase().replace(/_/g, " ");
    t4.long0 || !t4.longc || "albers conic equal area" !== e2 && "lambert azimuthal equal area" !== e2 || (t4.long0 = t4.longc), t4.lat_ts || !t4.lat1 || "stereographic south pole" !== e2 && "polar stereographic (variant b)" !== e2 ? t4.lat_ts || !t4.lat0 || "polar stereographic" !== e2 && "polar stereographic (variant a)" !== e2 || (t4.lat_ts = t4.lat0, t4.lat0 = H(t4.lat0 > 0 ? 90 : -90), delete t4.lat1) : (t4.lat0 = H(t4.lat1 > 0 ? 90 : -90), t4.lat_ts = t4.lat1, delete t4.lat1);
  }
  function Q(t4) {
    let e2 = { units: null, to_meter: void 0 };
    return "string" == typeof t4 ? (e2.units = t4.toLowerCase(), "metre" === e2.units && (e2.units = "meter"), "meter" === e2.units && (e2.to_meter = 1)) : (t4 == null ? void 0 : t4.name) && (e2.units = t4.name.toLowerCase(), "metre" === e2.units && (e2.units = "meter"), e2.to_meter = t4.conversion_factor), e2;
  }
  function Z(t4) {
    return "object" == typeof t4 ? t4.value * t4.unit.conversion_factor : t4;
  }
  function Y(t4, e2) {
    t4.ellipsoid.radius ? (e2.a = t4.ellipsoid.radius, e2.rf = 0) : (e2.a = Z(t4.ellipsoid.semi_major_axis), void 0 !== t4.ellipsoid.inverse_flattening ? e2.rf = t4.ellipsoid.inverse_flattening : void 0 !== t4.ellipsoid.semi_major_axis && void 0 !== t4.ellipsoid.semi_minor_axis && (e2.rf = e2.a / (e2.a - Z(t4.ellipsoid.semi_minor_axis))));
  }
  function J(t4, e2 = {}) {
    var _a;
    return t4 && "object" == typeof t4 ? "BoundCRS" === t4.type ? (J(t4.source_crs, e2), t4.transformation && ("NTv2" === ((_a = t4.transformation.method) == null ? void 0 : _a.name) ? e2.nadgrids = t4.transformation.parameters[0].value : e2.datum_params = t4.transformation.parameters.map((t5) => t5.value)), e2) : (Object.keys(t4).forEach((r2) => {
      var _a2, _b, _c;
      const i2 = t4[r2];
      if (null !== i2)
        switch (r2) {
          case "name":
            if (e2.srsCode)
              break;
            e2.name = i2, e2.srsCode = i2;
            break;
          case "type":
            "GeographicCRS" === i2 ? e2.projName = "longlat" : "ProjectedCRS" === i2 && (e2.projName = (_b = (_a2 = t4.conversion) == null ? void 0 : _a2.method) == null ? void 0 : _b.name);
            break;
          case "datum":
          case "datum_ensemble":
            i2.ellipsoid && (e2.ellps = i2.ellipsoid.name, Y(i2, e2)), i2.prime_meridian && (e2.from_greenwich = i2.prime_meridian.longitude * Math.PI / 180);
            break;
          case "ellipsoid":
            e2.ellps = i2.name, Y(i2, e2);
            break;
          case "prime_meridian":
            e2.long0 = (i2.longitude || 0) * Math.PI / 180;
            break;
          case "coordinate_system":
            if (i2.axis) {
              if (e2.axis = i2.axis.map((t5) => {
                const e3 = t5.direction;
                if ("east" === e3)
                  return "e";
                if ("north" === e3)
                  return "n";
                if ("west" === e3)
                  return "w";
                if ("south" === e3)
                  return "s";
                throw new Error(`Unknown axis direction: ${e3}`);
              }).join("") + "u", i2.unit) {
                const { units: t5, to_meter: r3 } = Q(i2.unit);
                e2.units = t5, e2.to_meter = r3;
              } else if ((_c = i2.axis[0]) == null ? void 0 : _c.unit) {
                const { units: t5, to_meter: r3 } = Q(i2.axis[0].unit);
                e2.units = t5, e2.to_meter = r3;
              }
            }
            break;
          case "id":
            i2.authority && i2.code && (e2.title = i2.authority + ":" + i2.code);
            break;
          case "conversion":
            i2.method && i2.method.name && (e2.projName = i2.method.name), i2.parameters && i2.parameters.forEach((t5) => {
              const r3 = t5.name.toLowerCase().replace(/\s+/g, "_"), i3 = t5.value;
              t5.unit && t5.unit.conversion_factor ? e2[r3] = i3 * t5.unit.conversion_factor : "degree" === t5.unit ? e2[r3] = i3 * Math.PI / 180 : e2[r3] = i3;
            });
            break;
          case "unit":
            i2.name && (e2.units = i2.name.toLowerCase(), "metre" === e2.units && (e2.units = "meter")), i2.conversion_factor && (e2.to_meter = i2.conversion_factor);
            break;
          case "base_crs":
            J(i2, e2), e2.datumCode = i2.id ? i2.id.authority + "_" + i2.id.code : i2.name;
        }
    }), void 0 !== e2.latitude_of_false_origin && (e2.lat0 = e2.latitude_of_false_origin), void 0 !== e2.longitude_of_false_origin && (e2.long0 = e2.longitude_of_false_origin), void 0 !== e2.latitude_of_standard_parallel && (e2.lat0 = e2.latitude_of_standard_parallel, e2.lat1 = e2.latitude_of_standard_parallel), void 0 !== e2.latitude_of_1st_standard_parallel && (e2.lat1 = e2.latitude_of_1st_standard_parallel), void 0 !== e2.latitude_of_2nd_standard_parallel && (e2.lat2 = e2.latitude_of_2nd_standard_parallel), void 0 !== e2.latitude_of_projection_centre && (e2.lat0 = e2.latitude_of_projection_centre), void 0 !== e2.longitude_of_projection_centre && (e2.longc = e2.longitude_of_projection_centre), void 0 !== e2.easting_at_false_origin && (e2.x0 = e2.easting_at_false_origin), void 0 !== e2.northing_at_false_origin && (e2.y0 = e2.northing_at_false_origin), void 0 !== e2.latitude_of_natural_origin && (e2.lat0 = e2.latitude_of_natural_origin), void 0 !== e2.longitude_of_natural_origin && (e2.long0 = e2.longitude_of_natural_origin), void 0 !== e2.longitude_of_origin && (e2.long0 = e2.longitude_of_origin), void 0 !== e2.false_easting && (e2.x0 = e2.false_easting), e2.easting_at_projection_centre && (e2.x0 = e2.easting_at_projection_centre), void 0 !== e2.false_northing && (e2.y0 = e2.false_northing), e2.northing_at_projection_centre && (e2.y0 = e2.northing_at_projection_centre), void 0 !== e2.standard_parallel_1 && (e2.lat1 = e2.standard_parallel_1), void 0 !== e2.standard_parallel_2 && (e2.lat2 = e2.standard_parallel_2), void 0 !== e2.scale_factor_at_natural_origin && (e2.k0 = e2.scale_factor_at_natural_origin), void 0 !== e2.scale_factor_at_projection_centre && (e2.k0 = e2.scale_factor_at_projection_centre), void 0 !== e2.scale_factor_on_pseudo_standard_parallel && (e2.k0 = e2.scale_factor_on_pseudo_standard_parallel), void 0 !== e2.azimuth && (e2.alpha = e2.azimuth), void 0 !== e2.azimuth_at_projection_centre && (e2.alpha = e2.azimuth_at_projection_centre), e2.angle_from_rectified_to_skew_grid && (e2.rectified_grid_angle = e2.angle_from_rectified_to_skew_grid), X(e2), e2) : t4;
  }
  var K = ["PROJECTEDCRS", "PROJCRS", "GEOGCS", "GEOCCS", "PROJCS", "LOCAL_CS", "GEODCRS", "GEODETICCRS", "GEODETICDATUM", "ENGCRS", "ENGINEERINGCRS"];
  function tt(t4, e2) {
    var r2 = e2[0], i2 = e2[1];
    !(r2 in t4) && i2 in t4 && (t4[r2] = t4[i2], 3 === e2.length && (t4[r2] = e2[2](t4[r2])));
  }
  function et(t4) {
    for (var e2 = Object.keys(t4), r2 = 0, i2 = e2.length; r2 < i2; ++r2) {
      var s2 = e2[r2];
      -1 !== K.indexOf(s2) && rt(t4[s2]), "object" == typeof t4[s2] && et(t4[s2]);
    }
  }
  function rt(t4) {
    if (t4.AUTHORITY) {
      var e2 = Object.keys(t4.AUTHORITY)[0];
      e2 && e2 in t4.AUTHORITY && (t4.title = e2 + ":" + t4.AUTHORITY[e2]);
    }
    if ("GEOGCS" === t4.type ? t4.projName = "longlat" : "LOCAL_CS" === t4.type ? (t4.projName = "identity", t4.local = true) : "object" == typeof t4.PROJECTION ? t4.projName = Object.keys(t4.PROJECTION)[0] : t4.projName = t4.PROJECTION, t4.AXIS) {
      for (var r2 = "", i2 = 0, s2 = t4.AXIS.length; i2 < s2; ++i2) {
        var n2 = [t4.AXIS[i2][0].toLowerCase(), t4.AXIS[i2][1].toLowerCase()];
        -1 !== n2[0].indexOf("north") || ("y" === n2[0] || "lat" === n2[0]) && "north" === n2[1] ? r2 += "n" : -1 !== n2[0].indexOf("south") || ("y" === n2[0] || "lat" === n2[0]) && "south" === n2[1] ? r2 += "s" : -1 !== n2[0].indexOf("east") || ("x" === n2[0] || "lon" === n2[0]) && "east" === n2[1] ? r2 += "e" : -1 === n2[0].indexOf("west") && ("x" !== n2[0] && "lon" !== n2[0] || "west" !== n2[1]) || (r2 += "w");
      }
      2 === r2.length && (r2 += "u"), 3 === r2.length && (t4.axis = r2);
    }
    t4.UNIT && (t4.units = t4.UNIT.name.toLowerCase(), "metre" === t4.units && (t4.units = "meter"), t4.UNIT.convert && ("GEOGCS" === t4.type ? t4.DATUM && t4.DATUM.SPHEROID && (t4.to_meter = t4.UNIT.convert * t4.DATUM.SPHEROID.a) : t4.to_meter = t4.UNIT.convert));
    var a2 = t4.GEOGCS;
    function o2(e3) {
      return e3 * (t4.to_meter || 1);
    }
    "GEOGCS" === t4.type && (a2 = t4), a2 && (a2.DATUM ? t4.datumCode = a2.DATUM.name.toLowerCase() : t4.datumCode = a2.name.toLowerCase(), "d_" === t4.datumCode.slice(0, 2) && (t4.datumCode = t4.datumCode.slice(2)), "new_zealand_1949" === t4.datumCode && (t4.datumCode = "nzgd49"), "wgs_1984" !== t4.datumCode && "world_geodetic_system_1984" !== t4.datumCode || ("Mercator_Auxiliary_Sphere" === t4.PROJECTION && (t4.sphere = true), t4.datumCode = "wgs84"), "belge_1972" === t4.datumCode && (t4.datumCode = "rnb72"), a2.DATUM && a2.DATUM.SPHEROID && (t4.ellps = a2.DATUM.SPHEROID.name.replace("_19", "").replace(/[Cc]larke\_18/, "clrk"), "international" === t4.ellps.toLowerCase().slice(0, 13) && (t4.ellps = "intl"), t4.a = a2.DATUM.SPHEROID.a, t4.rf = parseFloat(a2.DATUM.SPHEROID.rf, 10)), a2.DATUM && a2.DATUM.TOWGS84 && (t4.datum_params = a2.DATUM.TOWGS84), ~t4.datumCode.indexOf("osgb_1936") && (t4.datumCode = "osgb36"), ~t4.datumCode.indexOf("osni_1952") && (t4.datumCode = "osni52"), (~t4.datumCode.indexOf("tm65") || ~t4.datumCode.indexOf("geodetic_datum_of_1965")) && (t4.datumCode = "ire65"), "ch1903+" === t4.datumCode && (t4.datumCode = "ch1903"), ~t4.datumCode.indexOf("israel") && (t4.datumCode = "isr93")), t4.b && !isFinite(t4.b) && (t4.b = t4.a), t4.rectified_grid_angle && (t4.rectified_grid_angle = H(t4.rectified_grid_angle)), [["standard_parallel_1", "Standard_Parallel_1"], ["standard_parallel_1", "Latitude of 1st standard parallel"], ["standard_parallel_2", "Standard_Parallel_2"], ["standard_parallel_2", "Latitude of 2nd standard parallel"], ["false_easting", "False_Easting"], ["false_easting", "False easting"], ["false-easting", "Easting at false origin"], ["false_northing", "False_Northing"], ["false_northing", "False northing"], ["false_northing", "Northing at false origin"], ["central_meridian", "Central_Meridian"], ["central_meridian", "Longitude of natural origin"], ["central_meridian", "Longitude of false origin"], ["latitude_of_origin", "Latitude_Of_Origin"], ["latitude_of_origin", "Central_Parallel"], ["latitude_of_origin", "Latitude of natural origin"], ["latitude_of_origin", "Latitude of false origin"], ["scale_factor", "Scale_Factor"], ["k0", "scale_factor"], ["latitude_of_center", "Latitude_Of_Center"], ["latitude_of_center", "Latitude_of_center"], ["lat0", "latitude_of_center", H], ["longitude_of_center", "Longitude_Of_Center"], ["longitude_of_center", "Longitude_of_center"], ["longc", "longitude_of_center", H], ["x0", "false_easting", o2], ["y0", "false_northing", o2], ["long0", "central_meridian", H], ["lat0", "latitude_of_origin", H], ["lat0", "standard_parallel_1", H], ["lat1", "standard_parallel_1", H], ["lat2", "standard_parallel_2", H], ["azimuth", "Azimuth"], ["alpha", "azimuth", H], ["srsCode", "name"]].forEach(function(e3) {
      return tt(t4, e3);
    }), X(t4);
  }
  function it(t4) {
    if ("object" == typeof t4)
      return J(t4);
    const e2 = z(t4);
    var r2 = j(t4);
    if ("WKT2" === e2)
      return J(I(r2));
    var i2 = r2[0], s2 = {};
    return U(r2, s2), et(s2), s2[i2];
  }
  function st(t4) {
    var e2 = this;
    if (2 === arguments.length) {
      var r2 = arguments[1];
      "string" == typeof r2 ? "+" === r2.charAt(0) ? st[t4] = S(arguments[1]) : st[t4] = it(arguments[1]) : st[t4] = r2;
    } else if (1 === arguments.length) {
      if (Array.isArray(t4))
        return t4.map(function(t5) {
          return Array.isArray(t5) ? st.apply(e2, t5) : st(t5);
        });
      if ("string" == typeof t4) {
        if (t4 in st)
          return st[t4];
      } else
        "EPSG" in t4 ? st["EPSG:" + t4.EPSG] = t4 : "ESRI" in t4 ? st["ESRI:" + t4.ESRI] = t4 : "IAU2000" in t4 ? st["IAU2000:" + t4.IAU2000] = t4 : console.log(t4);
      return;
    }
  }
  function nt(t4) {
    return "string" == typeof t4;
  }
  function at(t4) {
    return t4 in st;
  }
  function ot(t4) {
    return 0 !== t4.indexOf("+") && -1 !== t4.indexOf("[") || "object" == typeof t4 && !("srsCode" in t4);
  }
  t(st);
  var ht = ["3857", "900913", "3785", "102113"];
  function lt2(t4) {
    var e2 = v(t4, "authority");
    if (e2) {
      var r2 = v(e2, "epsg");
      return r2 && ht.indexOf(r2) > -1;
    }
  }
  function ct(t4) {
    var e2 = v(t4, "extension");
    if (e2)
      return v(e2, "proj4");
  }
  function ut(t4) {
    return "+" === t4[0];
  }
  function dt(t4) {
    if (!nt(t4))
      return "projName" in t4 ? t4 : it(t4);
    if (at(t4))
      return st[t4];
    if (ot(t4)) {
      var e2 = it(t4);
      if (lt2(e2))
        return st["EPSG:3857"];
      var r2 = ct(e2);
      return r2 ? S(r2) : e2;
    }
    return ut(t4) ? S(t4) : void 0;
  }
  function pt(t4, e2) {
    var r2, i2;
    if (t4 = t4 || {}, !e2)
      return t4;
    for (i2 in e2)
      void 0 !== (r2 = e2[i2]) && (t4[i2] = r2);
    return t4;
  }
  function ft(t4, e2, r2) {
    var i2 = t4 * e2;
    return r2 / Math.sqrt(1 - i2 * i2);
  }
  function _t(t4) {
    return t4 < 0 ? -1 : 1;
  }
  function yt(t4) {
    return Math.abs(t4) <= x ? t4 : t4 - _t(t4) * g;
  }
  function mt(t4, e2, r2) {
    var i2 = t4 * r2, s2 = 0.5 * t4;
    return i2 = Math.pow((1 - i2) / (1 + i2), s2), Math.tan(0.5 * (c - e2)) / i2;
  }
  function gt2(t4, e2) {
    for (var r2, i2, s2 = 0.5 * t4, n2 = c - 2 * Math.atan(e2), a2 = 0; a2 <= 15; a2++)
      if (r2 = t4 * Math.sin(n2), n2 += i2 = c - 2 * Math.atan(e2 * Math.pow((1 - r2) / (1 + r2), s2)) - n2, Math.abs(i2) <= 1e-10)
        return n2;
    return -9999;
  }
  function xt() {
    var t4 = this.b / this.a;
    this.es = 1 - t4 * t4, "x0" in this || (this.x0 = 0), "y0" in this || (this.y0 = 0), this.e = Math.sqrt(this.es), this.lat_ts ? this.sphere ? this.k0 = Math.cos(this.lat_ts) : this.k0 = ft(this.e, Math.sin(this.lat_ts), Math.cos(this.lat_ts)) : this.k0 || (this.k ? this.k0 = this.k : this.k0 = 1);
  }
  function Mt(t4) {
    var e2, r2, i2 = t4.x, s2 = t4.y;
    if (s2 * y > 90 && s2 * y < -90 && i2 * y > 180 && i2 * y < -180)
      return null;
    if (Math.abs(Math.abs(s2) - c) <= f)
      return null;
    if (this.sphere)
      e2 = this.x0 + this.a * this.k0 * yt(i2 - this.long0), r2 = this.y0 + this.a * this.k0 * Math.log(Math.tan(m + 0.5 * s2));
    else {
      var n2 = Math.sin(s2), a2 = mt(this.e, s2, n2);
      e2 = this.x0 + this.a * this.k0 * yt(i2 - this.long0), r2 = this.y0 - this.a * this.k0 * Math.log(a2);
    }
    return t4.x = e2, t4.y = r2, t4;
  }
  function wt(t4) {
    var e2, r2, i2 = t4.x - this.x0, s2 = t4.y - this.y0;
    if (this.sphere)
      r2 = c - 2 * Math.atan(Math.exp(-s2 / (this.a * this.k0)));
    else {
      var n2 = Math.exp(-s2 / (this.a * this.k0));
      if (-9999 === (r2 = gt2(this.e, n2)))
        return null;
    }
    return e2 = yt(this.long0 + i2 / (this.a * this.k0)), t4.x = e2, t4.y = r2, t4;
  }
  function bt() {
  }
  function vt(t4) {
    return t4;
  }
  var St = [{ init: xt, forward: Mt, inverse: wt, names: ["Mercator", "Popular Visualisation Pseudo Mercator", "Mercator_1SP", "Mercator_Auxiliary_Sphere", "Mercator_Variant_A", "merc"] }, { init: bt, forward: vt, inverse: vt, names: ["longlat", "identity"] }], Ct = {}, Et = [];
  function Pt(t4, e2) {
    var r2 = Et.length;
    return t4.names ? (Et[r2] = t4, t4.names.forEach(function(t5) {
      Ct[t5.toLowerCase()] = r2;
    }), this) : (console.log(e2), true);
  }
  function Tt(t4) {
    return t4.replace(/[-\(\)\s]+/g, " ").trim().replace(/ /g, "_");
  }
  function It(t4) {
    if (!t4)
      return false;
    var e2 = t4.toLowerCase();
    return void 0 !== Ct[e2] && Et[Ct[e2]] || (e2 = Tt(e2)) in Ct && Et[Ct[e2]] ? Et[Ct[e2]] : void 0;
  }
  function zt() {
    St.forEach(Pt);
  }
  var At2 = { start: zt, add: Pt, get: It }, Ot = { MERIT: { a: 6378137, rf: 298.257, ellipseName: "MERIT 1983" }, SGS85: { a: 6378136, rf: 298.257, ellipseName: "Soviet Geodetic System 85" }, GRS80: { a: 6378137, rf: 298.257222101, ellipseName: "GRS 1980(IUGG, 1980)" }, IAU76: { a: 6378140, rf: 298.257, ellipseName: "IAU 1976" }, airy: { a: 6377563396e-3, b: 635625691e-2, ellipseName: "Airy 1830" }, APL4: { a: 6378137, rf: 298.25, ellipseName: "Appl. Physics. 1965" }, NWL9D: { a: 6378145, rf: 298.25, ellipseName: "Naval Weapons Lab., 1965" }, mod_airy: { a: 6377340189e-3, b: 6356034446e-3, ellipseName: "Modified Airy" }, andrae: { a: 637710443e-2, rf: 300, ellipseName: "Andrae 1876 (Den., Iclnd.)" }, aust_SA: { a: 6378160, rf: 298.25, ellipseName: "Australian Natl & S. Amer. 1969" }, GRS67: { a: 6378160, rf: 298.247167427, ellipseName: "GRS 67(IUGG 1967)" }, bessel: { a: 6377397155e-3, rf: 299.1528128, ellipseName: "Bessel 1841" }, bess_nam: { a: 6377483865e-3, rf: 299.1528128, ellipseName: "Bessel 1841 (Namibia)" }, clrk66: { a: 63782064e-1, b: 63565838e-1, ellipseName: "Clarke 1866" }, clrk80: { a: 6378249145e-3, rf: 293.4663, ellipseName: "Clarke 1880 mod." }, clrk80ign: { a: 63782492e-1, b: 6356515, rf: 293.4660213, ellipseName: "Clarke 1880 (IGN)" }, clrk58: { a: 6378293645208759e-9, rf: 294.2606763692654, ellipseName: "Clarke 1858" }, CPM: { a: 63757387e-1, rf: 334.29, ellipseName: "Comm. des Poids et Mesures 1799" }, delmbr: { a: 6376428, rf: 311.5, ellipseName: "Delambre 1810 (Belgium)" }, engelis: { a: 637813605e-2, rf: 298.2566, ellipseName: "Engelis 1985" }, evrst30: { a: 6377276345e-3, rf: 300.8017, ellipseName: "Everest 1830" }, evrst48: { a: 6377304063e-3, rf: 300.8017, ellipseName: "Everest 1948" }, evrst56: { a: 6377301243e-3, rf: 300.8017, ellipseName: "Everest 1956" }, evrst69: { a: 6377295664e-3, rf: 300.8017, ellipseName: "Everest 1969" }, evrstSS: { a: 6377298556e-3, rf: 300.8017, ellipseName: "Everest (Sabah & Sarawak)" }, fschr60: { a: 6378166, rf: 298.3, ellipseName: "Fischer (Mercury Datum) 1960" }, fschr60m: { a: 6378155, rf: 298.3, ellipseName: "Fischer 1960" }, fschr68: { a: 6378150, rf: 298.3, ellipseName: "Fischer 1968" }, helmert: { a: 6378200, rf: 298.3, ellipseName: "Helmert 1906" }, hough: { a: 6378270, rf: 297, ellipseName: "Hough" }, intl: { a: 6378388, rf: 297, ellipseName: "International 1909 (Hayford)" }, kaula: { a: 6378163, rf: 298.24, ellipseName: "Kaula 1961" }, lerch: { a: 6378139, rf: 298.257, ellipseName: "Lerch 1979" }, mprts: { a: 6397300, rf: 191, ellipseName: "Maupertius 1738" }, new_intl: { a: 63781575e-1, b: 63567722e-1, ellipseName: "New International 1967" }, plessis: { a: 6376523, rf: 6355863, ellipseName: "Plessis 1817 (France)" }, krass: { a: 6378245, rf: 298.3, ellipseName: "Krassovsky, 1942" }, SEasia: { a: 6378155, b: 63567733205e-4, ellipseName: "Southeast Asia" }, walbeck: { a: 6376896, b: 63558348467e-4, ellipseName: "Walbeck" }, WGS60: { a: 6378165, rf: 298.3, ellipseName: "WGS 60" }, WGS66: { a: 6378145, rf: 298.25, ellipseName: "WGS 66" }, WGS7: { a: 6378135, rf: 298.26, ellipseName: "WGS 72" }, WGS84: { a: 6378137, rf: 298.257223563, ellipseName: "WGS 84" }, sphere: { a: 6370997, b: 6370997, ellipseName: "Normal Sphere (r=6370997)" } };
  const Nt = Ot.WGS84;
  function Rt(t4, e2, r2, i2) {
    var s2 = t4 * t4, n2 = e2 * e2, a2 = (s2 - n2) / s2, o2 = 0;
    return i2 ? (s2 = (t4 *= 1 - a2 * (u + a2 * (d + a2 * p))) * t4, a2 = 0) : o2 = Math.sqrt(a2), { es: a2, e: o2, ep2: (s2 - n2) / n2 };
  }
  function $t(t4, e2, r2, i2, s2) {
    if (!t4) {
      var n2 = v(Ot, i2);
      n2 || (n2 = Nt), t4 = n2.a, e2 = n2.b, r2 = n2.rf;
    }
    return r2 && !e2 && (e2 = (1 - 1 / r2) * t4), (0 === r2 || Math.abs(t4 - e2) < f) && (s2 = true, e2 = t4), { a: t4, b: e2, rf: r2, sphere: s2 };
  }
  var Vt = { wgs84: { towgs84: "0,0,0", ellipse: "WGS84", datumName: "WGS84" }, ch1903: { towgs84: "674.374,15.056,405.346", ellipse: "bessel", datumName: "swiss" }, ggrs87: { towgs84: "-199.87,74.79,246.62", ellipse: "GRS80", datumName: "Greek_Geodetic_Reference_System_1987" }, nad83: { towgs84: "0,0,0", ellipse: "GRS80", datumName: "North_American_Datum_1983" }, nad27: { nadgrids: "@conus,@alaska,@ntv2_0.gsb,@ntv1_can.dat", ellipse: "clrk66", datumName: "North_American_Datum_1927" }, potsdam: { towgs84: "598.1,73.7,418.2,0.202,0.045,-2.455,6.7", ellipse: "bessel", datumName: "Potsdam Rauenberg 1950 DHDN" }, carthage: { towgs84: "-263.0,6.0,431.0", ellipse: "clark80", datumName: "Carthage 1934 Tunisia" }, hermannskogel: { towgs84: "577.326,90.129,463.919,5.137,1.474,5.297,2.4232", ellipse: "bessel", datumName: "Hermannskogel" }, mgi: { towgs84: "577.326,90.129,463.919,5.137,1.474,5.297,2.4232", ellipse: "bessel", datumName: "Militar-Geographische Institut" }, osni52: { towgs84: "482.530,-130.596,564.557,-1.042,-0.214,-0.631,8.15", ellipse: "airy", datumName: "Irish National" }, ire65: { towgs84: "482.530,-130.596,564.557,-1.042,-0.214,-0.631,8.15", ellipse: "mod_airy", datumName: "Ireland 1965" }, rassadiran: { towgs84: "-133.63,-157.5,-158.62", ellipse: "intl", datumName: "Rassadiran" }, nzgd49: { towgs84: "59.47,-5.04,187.44,0.47,-0.1,1.024,-4.5993", ellipse: "intl", datumName: "New Zealand Geodetic Datum 1949" }, osgb36: { towgs84: "446.448,-125.157,542.060,0.1502,0.2470,0.8421,-20.4894", ellipse: "airy", datumName: "Ordnance Survey of Great Britain 1936" }, s_jtsk: { towgs84: "589,76,480", ellipse: "bessel", datumName: "S-JTSK (Ferro)" }, beduaram: { towgs84: "-106,-87,188", ellipse: "clrk80", datumName: "Beduaram" }, gunung_segara: { towgs84: "-403,684,41", ellipse: "bessel", datumName: "Gunung Segara Jakarta" }, rnb72: { towgs84: "106.869,-52.2978,103.724,-0.33657,0.456955,-1.84218,1", ellipse: "intl", datumName: "Reseau National Belge 1972" }, EPSG_5451: { towgs84: "6.41,-49.05,-11.28,1.5657,0.5242,6.9718,-5.7649" }, IGNF_LURESG: { towgs84: "-192.986,13.673,-39.309,-0.4099,-2.9332,2.6881,0.43" }, EPSG_4614: { towgs84: "-119.4248,-303.65872,-11.00061,1.164298,0.174458,1.096259,3.657065" }, EPSG_4615: { towgs84: "-494.088,-312.129,279.877,-1.423,-1.013,1.59,-0.748" }, ESRI_37241: { towgs84: "-76.822,257.457,-12.817,2.136,-0.033,-2.392,-0.031" }, ESRI_37249: { towgs84: "-440.296,58.548,296.265,1.128,10.202,4.559,-0.438" }, ESRI_37245: { towgs84: "-511.151,-181.269,139.609,1.05,2.703,1.798,3.071" }, EPSG_4178: { towgs84: "24.9,-126.4,-93.2,-0.063,-0.247,-0.041,1.01" }, EPSG_4622: { towgs84: "-472.29,-5.63,-304.12,0.4362,-0.8374,0.2563,1.8984" }, EPSG_4625: { towgs84: "126.93,547.94,130.41,-2.7867,5.1612,-0.8584,13.8227" }, EPSG_5252: { towgs84: "0.023,0.036,-0.068,0.00176,0.00912,-0.01136,0.00439" }, EPSG_4314: { towgs84: "597.1,71.4,412.1,0.894,0.068,-1.563,7.58" }, EPSG_4282: { towgs84: "-178.3,-316.7,-131.5,5.278,6.077,10.979,19.166" }, EPSG_4231: { towgs84: "-83.11,-97.38,-117.22,0.0276,-0.2167,0.2147,0.1218" }, EPSG_4274: { towgs84: "-230.994,102.591,25.199,0.633,-0.239,0.9,1.95" }, EPSG_4134: { towgs84: "-180.624,-225.516,173.919,-0.81,-1.898,8.336,16.71006" }, EPSG_4254: { towgs84: "18.38,192.45,96.82,0.056,-0.142,-0.2,-0.0013" }, EPSG_4159: { towgs84: "-194.513,-63.978,-25.759,-3.4027,3.756,-3.352,-0.9175" }, EPSG_4687: { towgs84: "0.072,-0.507,-0.245,0.0183,-0.0003,0.007,-0.0093" }, EPSG_4227: { towgs84: "-83.58,-397.54,458.78,-17.595,-2.847,4.256,3.225" }, EPSG_4746: { towgs84: "599.4,72.4,419.2,-0.062,-0.022,-2.723,6.46" }, EPSG_4745: { towgs84: "612.4,77,440.2,-0.054,0.057,-2.797,2.55" }, EPSG_6311: { towgs84: "8.846,-4.394,-1.122,-0.00237,-0.146528,0.130428,0.783926" }, EPSG_4289: { towgs84: "565.7381,50.4018,465.2904,-1.91514,1.60363,-9.09546,4.07244" }, EPSG_4230: { towgs84: "-68.863,-134.888,-111.49,-0.53,-0.14,0.57,-3.4" }, EPSG_4154: { towgs84: "-123.02,-158.95,-168.47" }, EPSG_4156: { towgs84: "570.8,85.7,462.8,4.998,1.587,5.261,3.56" }, EPSG_4299: { towgs84: "482.5,-130.6,564.6,-1.042,-0.214,-0.631,8.15" }, EPSG_4179: { towgs84: "33.4,-146.6,-76.3,-0.359,-0.053,0.844,-0.84" }, EPSG_4313: { towgs84: "-106.8686,52.2978,-103.7239,0.3366,-0.457,1.8422,-1.2747" }, EPSG_4194: { towgs84: "163.511,127.533,-159.789" }, EPSG_4195: { towgs84: "105,326,-102.5" }, EPSG_4196: { towgs84: "-45,417,-3.5" }, EPSG_4611: { towgs84: "-162.619,-276.959,-161.764,0.067753,-2.243649,-1.158827,-1.094246" }, EPSG_4633: { towgs84: "137.092,131.66,91.475,-1.9436,-11.5993,-4.3321,-7.4824" }, EPSG_4641: { towgs84: "-408.809,366.856,-412.987,1.8842,-0.5308,2.1655,-121.0993" }, EPSG_4643: { towgs84: "-480.26,-438.32,-643.429,16.3119,20.1721,-4.0349,-111.7002" }, EPSG_4300: { towgs84: "482.5,-130.6,564.6,-1.042,-0.214,-0.631,8.15" }, EPSG_4188: { towgs84: "482.5,-130.6,564.6,-1.042,-0.214,-0.631,8.15" }, EPSG_4660: { towgs84: "982.6087,552.753,-540.873,32.39344,-153.25684,-96.2266,16.805" }, EPSG_4662: { towgs84: "97.295,-263.247,310.882,-1.5999,0.8386,3.1409,13.3259" }, EPSG_3906: { towgs84: "577.88891,165.22205,391.18289,4.9145,-0.94729,-13.05098,7.78664" }, EPSG_4307: { towgs84: "-209.3622,-87.8162,404.6198,0.0046,3.4784,0.5805,-1.4547" }, EPSG_6892: { towgs84: "-76.269,-16.683,68.562,-6.275,10.536,-4.286,-13.686" }, EPSG_4690: { towgs84: "221.597,152.441,176.523,2.403,1.3893,0.884,11.4648" }, EPSG_4691: { towgs84: "218.769,150.75,176.75,3.5231,2.0037,1.288,10.9817" }, EPSG_4629: { towgs84: "72.51,345.411,79.241,-1.5862,-0.8826,-0.5495,1.3653" }, EPSG_4630: { towgs84: "165.804,216.213,180.26,-0.6251,-0.4515,-0.0721,7.4111" }, EPSG_4692: { towgs84: "217.109,86.452,23.711,0.0183,-0.0003,0.007,-0.0093" }, EPSG_9333: { towgs84: "0,0,0,-8.393,0.749,-10.276,0" }, EPSG_9059: { towgs84: "0,0,0" }, EPSG_4312: { towgs84: "601.705,84.263,485.227,4.7354,1.3145,5.393,-2.3887" }, EPSG_4123: { towgs84: "-96.062,-82.428,-121.753,4.801,0.345,-1.376,1.496" }, EPSG_4309: { towgs84: "-124.45,183.74,44.64,-0.4384,0.5446,-0.9706,-2.1365" }, ESRI_104106: { towgs84: "-283.088,-70.693,117.445,-1.157,0.059,-0.652,-4.058" }, EPSG_4281: { towgs84: "-219.247,-73.802,269.529" }, EPSG_4322: { towgs84: "0,0,4.5" }, EPSG_4324: { towgs84: "0,0,1.9" }, EPSG_4284: { towgs84: "43.822,-108.842,-119.585,1.455,-0.761,0.737,0.549" }, EPSG_4277: { towgs84: "446.448,-125.157,542.06,0.15,0.247,0.842,-20.489" }, EPSG_4207: { towgs84: "-282.1,-72.2,120,-1.529,0.145,-0.89,-4.46" }, EPSG_4688: { towgs84: "347.175,1077.618,2623.677,33.9058,-70.6776,9.4013,186.0647" }, EPSG_4689: { towgs84: "410.793,54.542,80.501,-2.5596,-2.3517,-0.6594,17.3218" }, EPSG_4720: { towgs84: "0,0,4.5" }, EPSG_4273: { towgs84: "278.3,93,474.5,7.889,0.05,-6.61,6.21" }, EPSG_4240: { towgs84: "204.64,834.74,293.8" }, EPSG_4817: { towgs84: "278.3,93,474.5,7.889,0.05,-6.61,6.21" }, ESRI_104131: { towgs84: "426.62,142.62,460.09,4.98,4.49,-12.42,-17.1" }, EPSG_4265: { towgs84: "-104.1,-49.1,-9.9,0.971,-2.917,0.714,-11.68" }, EPSG_4263: { towgs84: "-111.92,-87.85,114.5,1.875,0.202,0.219,0.032" }, EPSG_4298: { towgs84: "-689.5937,623.84046,-65.93566,-0.02331,1.17094,-0.80054,5.88536" }, EPSG_4270: { towgs84: "-253.4392,-148.452,386.5267,0.15605,0.43,-0.1013,-0.0424" }, EPSG_4229: { towgs84: "-121.8,98.1,-10.7" }, EPSG_4220: { towgs84: "-55.5,-348,-229.2" }, EPSG_4214: { towgs84: "12.646,-155.176,-80.863" }, EPSG_4232: { towgs84: "-345,3,223" }, EPSG_4238: { towgs84: "-1.977,-13.06,-9.993,0.364,0.254,0.689,-1.037" }, EPSG_4168: { towgs84: "-170,33,326" }, EPSG_4131: { towgs84: "199,931,318.9" }, EPSG_4152: { towgs84: "-0.9102,2.0141,0.5602,0.029039,0.010065,0.010101,0" }, EPSG_5228: { towgs84: "572.213,85.334,461.94,4.9732,1.529,5.2484,3.5378" }, EPSG_8351: { towgs84: "485.021,169.465,483.839,7.786342,4.397554,4.102655,0" }, EPSG_4683: { towgs84: "-127.62,-67.24,-47.04,-3.068,4.903,1.578,-1.06" }, EPSG_4133: { towgs84: "0,0,0" }, EPSG_7373: { towgs84: "0.819,-0.5762,-1.6446,-0.00378,-0.03317,0.00318,0.0693" }, EPSG_9075: { towgs84: "-0.9102,2.0141,0.5602,0.029039,0.010065,0.010101,0" }, EPSG_9072: { towgs84: "-0.9102,2.0141,0.5602,0.029039,0.010065,0.010101,0" }, EPSG_9294: { towgs84: "1.16835,-1.42001,-2.24431,-0.00822,-0.05508,0.01818,0.23388" }, EPSG_4212: { towgs84: "-267.434,173.496,181.814,-13.4704,8.7154,7.3926,14.7492" }, EPSG_4191: { towgs84: "-44.183,-0.58,-38.489,2.3867,2.7072,-3.5196,-8.2703" }, EPSG_4237: { towgs84: "52.684,-71.194,-13.975,-0.312,-0.1063,-0.3729,1.0191" }, EPSG_4740: { towgs84: "-1.08,-0.27,-0.9" }, EPSG_4124: { towgs84: "419.3836,99.3335,591.3451,0.850389,1.817277,-7.862238,-0.99496" }, EPSG_5681: { towgs84: "584.9636,107.7175,413.8067,1.1155,0.2824,-3.1384,7.9922" }, EPSG_4141: { towgs84: "23.772,17.49,17.859,-0.3132,-1.85274,1.67299,-5.4262" }, EPSG_4204: { towgs84: "-85.645,-273.077,-79.708,2.289,-1.421,2.532,3.194" }, EPSG_4319: { towgs84: "226.702,-193.337,-35.371,-2.229,-4.391,9.238,0.9798" }, EPSG_4200: { towgs84: "24.82,-131.21,-82.66" }, EPSG_4130: { towgs84: "0,0,0" }, EPSG_4127: { towgs84: "-82.875,-57.097,-156.768,-2.158,1.524,-0.982,-0.359" }, EPSG_4149: { towgs84: "674.374,15.056,405.346" }, EPSG_4617: { towgs84: "-0.991,1.9072,0.5129,1.25033e-7,4.6785e-8,5.6529e-8,0" }, EPSG_4663: { towgs84: "-210.502,-66.902,-48.476,2.094,-15.067,-5.817,0.485" }, EPSG_4664: { towgs84: "-211.939,137.626,58.3,-0.089,0.251,0.079,0.384" }, EPSG_4665: { towgs84: "-105.854,165.589,-38.312,-0.003,-0.026,0.024,-0.048" }, EPSG_4666: { towgs84: "631.392,-66.551,481.442,1.09,-4.445,-4.487,-4.43" }, EPSG_4756: { towgs84: "-192.873,-39.382,-111.202,-0.00205,-0.0005,0.00335,0.0188" }, EPSG_4723: { towgs84: "-179.483,-69.379,-27.584,-7.862,8.163,6.042,-13.925" }, EPSG_4726: { towgs84: "8.853,-52.644,180.304,-0.393,-2.323,2.96,-24.081" }, EPSG_4267: { towgs84: "-8.0,160.0,176.0" }, EPSG_5365: { towgs84: "-0.16959,0.35312,0.51846,0.03385,-0.16325,0.03446,0.03693" }, EPSG_4218: { towgs84: "304.5,306.5,-318.1" }, EPSG_4242: { towgs84: "-33.722,153.789,94.959,-8.581,-4.478,4.54,8.95" }, EPSG_4216: { towgs84: "-292.295,248.758,429.447,4.9971,2.99,6.6906,1.0289" }, ESRI_104105: { towgs84: "631.392,-66.551,481.442,1.09,-4.445,-4.487,-4.43" }, ESRI_104129: { towgs84: "0,0,0" }, EPSG_4673: { towgs84: "174.05,-25.49,112.57" }, EPSG_4202: { towgs84: "-124,-60,154" }, EPSG_4203: { towgs84: "-117.763,-51.51,139.061,0.292,0.443,0.277,-0.191" }, EPSG_3819: { towgs84: "595.48,121.69,515.35,4.115,-2.9383,0.853,-3.408" }, EPSG_8694: { towgs84: "-93.799,-132.737,-219.073,-1.844,0.648,-6.37,-0.169" }, EPSG_4145: { towgs84: "275.57,676.78,229.6" }, EPSG_4283: { towgs84: "61.55,-10.87,-40.19,39.4924,32.7221,32.8979,-9.994" }, EPSG_4317: { towgs84: "2.3287,-147.0425,-92.0802,-0.3092483,0.32482185,0.49729934,5.68906266" }, EPSG_4272: { towgs84: "59.47,-5.04,187.44,0.47,-0.1,1.024,-4.5993" }, EPSG_4248: { towgs84: "-307.7,265.3,-363.5" }, EPSG_5561: { towgs84: "24,-121,-76" }, EPSG_5233: { towgs84: "-0.293,766.95,87.713,0.195704,1.695068,3.473016,-0.039338" }, ESRI_104130: { towgs84: "-86,-98,-119" }, ESRI_104102: { towgs84: "682,-203,480" }, ESRI_37207: { towgs84: "7,-10,-26" }, EPSG_4675: { towgs84: "59.935,118.4,-10.871" }, ESRI_104109: { towgs84: "-89.121,-348.182,260.871" }, ESRI_104112: { towgs84: "-185.583,-230.096,281.361" }, ESRI_104113: { towgs84: "25.1,-275.6,222.6" }, IGNF_WGS72G: { towgs84: "0,12,6" }, IGNF_NTFG: { towgs84: "-168,-60,320" }, IGNF_EFATE57G: { towgs84: "-127,-769,472" }, IGNF_PGP50G: { towgs84: "324.8,153.6,172.1" }, IGNF_REUN47G: { towgs84: "94,-948,-1262" }, IGNF_CSG67G: { towgs84: "-186,230,110" }, IGNF_GUAD48G: { towgs84: "-467,-16,-300" }, IGNF_TAHI51G: { towgs84: "162,117,154" }, IGNF_TAHAAG: { towgs84: "65,342,77" }, IGNF_NUKU72G: { towgs84: "84,274,65" }, IGNF_PETRELS72G: { towgs84: "365,194,166" }, IGNF_WALL78G: { towgs84: "253,-133,-127" }, IGNF_MAYO50G: { towgs84: "-382,-59,-262" }, IGNF_TANNAG: { towgs84: "-139,-967,436" }, IGNF_IGN72G: { towgs84: "-13,-348,292" }, IGNF_ATIGG: { towgs84: "1118,23,66" }, IGNF_FANGA84G: { towgs84: "150.57,158.33,118.32" }, IGNF_RUSAT84G: { towgs84: "202.13,174.6,-15.74" }, IGNF_KAUE70G: { towgs84: "126.74,300.1,-75.49" }, IGNF_MOP90G: { towgs84: "-10.8,-1.8,12.77" }, IGNF_MHPF67G: { towgs84: "338.08,212.58,-296.17" }, IGNF_TAHI79G: { towgs84: "160.61,116.05,153.69" }, IGNF_ANAA92G: { towgs84: "1.5,3.84,4.81" }, IGNF_MARQUI72G: { towgs84: "330.91,-13.92,58.56" }, IGNF_APAT86G: { towgs84: "143.6,197.82,74.05" }, IGNF_TUBU69G: { towgs84: "237.17,171.61,-77.84" }, IGNF_STPM50G: { towgs84: "11.363,424.148,373.13" }, EPSG_4150: { towgs84: "674.374,15.056,405.346" }, EPSG_4754: { towgs84: "-208.4058,-109.8777,-2.5764" }, ESRI_104101: { towgs84: "374,150,588" }, EPSG_4693: { towgs84: "0,-0.15,0.68" }, EPSG_6207: { towgs84: "293.17,726.18,245.36" }, EPSG_4153: { towgs84: "-133.63,-157.5,-158.62" }, EPSG_4132: { towgs84: "-241.54,-163.64,396.06" }, EPSG_4221: { towgs84: "-154.5,150.7,100.4" }, EPSG_4266: { towgs84: "-80.7,-132.5,41.1" }, EPSG_4193: { towgs84: "-70.9,-151.8,-41.4" }, EPSG_5340: { towgs84: "-0.41,0.46,-0.35" }, EPSG_4246: { towgs84: "-294.7,-200.1,525.5" }, EPSG_4318: { towgs84: "-3.2,-5.7,2.8" }, EPSG_4121: { towgs84: "-199.87,74.79,246.62" }, EPSG_4223: { towgs84: "-260.1,5.5,432.2" }, EPSG_4158: { towgs84: "-0.465,372.095,171.736" }, EPSG_4285: { towgs84: "-128.16,-282.42,21.93" }, EPSG_4613: { towgs84: "-404.78,685.68,45.47" }, EPSG_4607: { towgs84: "195.671,332.517,274.607" }, EPSG_4475: { towgs84: "-381.788,-57.501,-256.673" }, EPSG_4208: { towgs84: "-157.84,308.54,-146.6" }, EPSG_4743: { towgs84: "70.995,-335.916,262.898" }, EPSG_4710: { towgs84: "-323.65,551.39,-491.22" }, EPSG_7881: { towgs84: "-0.077,0.079,0.086" }, EPSG_4682: { towgs84: "283.729,735.942,261.143" }, EPSG_4739: { towgs84: "-156,-271,-189" }, EPSG_4679: { towgs84: "-80.01,253.26,291.19" }, EPSG_4750: { towgs84: "-56.263,16.136,-22.856" }, EPSG_4644: { towgs84: "-10.18,-350.43,291.37" }, EPSG_4695: { towgs84: "-103.746,-9.614,-255.95" }, EPSG_4292: { towgs84: "-355,21,72" }, EPSG_4302: { towgs84: "-61.702,284.488,472.052" }, EPSG_4143: { towgs84: "-124.76,53,466.79" }, EPSG_4606: { towgs84: "-153,153,307" }, EPSG_4699: { towgs84: "-770.1,158.4,-498.2" }, EPSG_4247: { towgs84: "-273.5,110.6,-357.9" }, EPSG_4160: { towgs84: "8.88,184.86,106.69" }, EPSG_4161: { towgs84: "-233.43,6.65,173.64" }, EPSG_9251: { towgs84: "-9.5,122.9,138.2" }, EPSG_9253: { towgs84: "-78.1,101.6,133.3" }, EPSG_4297: { towgs84: "-198.383,-240.517,-107.909" }, EPSG_4269: { towgs84: "0,0,0" }, EPSG_4301: { towgs84: "-147,506,687" }, EPSG_4618: { towgs84: "-59,-11,-52" }, EPSG_4612: { towgs84: "0,0,0" }, EPSG_4678: { towgs84: "44.585,-131.212,-39.544" }, EPSG_4250: { towgs84: "-130,29,364" }, EPSG_4144: { towgs84: "214,804,268" }, EPSG_4147: { towgs84: "-17.51,-108.32,-62.39" }, EPSG_4259: { towgs84: "-254.1,-5.36,-100.29" }, EPSG_4164: { towgs84: "-76,-138,67" }, EPSG_4211: { towgs84: "-378.873,676.002,-46.255" }, EPSG_4182: { towgs84: "-422.651,-172.995,84.02" }, EPSG_4224: { towgs84: "-143.87,243.37,-33.52" }, EPSG_4225: { towgs84: "-205.57,168.77,-4.12" }, EPSG_5527: { towgs84: "-67.35,3.88,-38.22" }, EPSG_4752: { towgs84: "98,390,-22" }, EPSG_4310: { towgs84: "-30,190,89" }, EPSG_9248: { towgs84: "-192.26,65.72,132.08" }, EPSG_4680: { towgs84: "124.5,-63.5,-281" }, EPSG_4701: { towgs84: "-79.9,-158,-168.9" }, EPSG_4706: { towgs84: "-146.21,112.63,4.05" }, EPSG_4805: { towgs84: "682,-203,480" }, EPSG_4201: { towgs84: "-165,-11,206" }, EPSG_4210: { towgs84: "-157,-2,-299" }, EPSG_4183: { towgs84: "-104,167,-38" }, EPSG_4139: { towgs84: "11,72,-101" }, EPSG_4668: { towgs84: "-86,-98,-119" }, EPSG_4717: { towgs84: "-2,151,181" }, EPSG_4732: { towgs84: "102,52,-38" }, EPSG_4280: { towgs84: "-377,681,-50" }, EPSG_4209: { towgs84: "-138,-105,-289" }, EPSG_4261: { towgs84: "31,146,47" }, EPSG_4658: { towgs84: "-73,46,-86" }, EPSG_4721: { towgs84: "265.025,384.929,-194.046" }, EPSG_4222: { towgs84: "-136,-108,-292" }, EPSG_4601: { towgs84: "-255,-15,71" }, EPSG_4602: { towgs84: "725,685,536" }, EPSG_4603: { towgs84: "72,213.7,93" }, EPSG_4605: { towgs84: "9,183,236" }, EPSG_4621: { towgs84: "137,248,-430" }, EPSG_4657: { towgs84: "-28,199,5" }, EPSG_4316: { towgs84: "103.25,-100.4,-307.19" }, EPSG_4642: { towgs84: "-13,-348,292" }, EPSG_4698: { towgs84: "145,-187,103" }, EPSG_4192: { towgs84: "-206.1,-174.7,-87.7" }, EPSG_4311: { towgs84: "-265,120,-358" }, EPSG_4135: { towgs84: "58,-283,-182" }, ESRI_104138: { towgs84: "198,-226,-347" }, EPSG_4245: { towgs84: "-11,851,5" }, EPSG_4142: { towgs84: "-125,53,467" }, EPSG_4213: { towgs84: "-106,-87,188" }, EPSG_4253: { towgs84: "-133,-77,-51" }, EPSG_4129: { towgs84: "-132,-110,-335" }, EPSG_4713: { towgs84: "-77,-128,142" }, EPSG_4239: { towgs84: "217,823,299" }, EPSG_4146: { towgs84: "295,736,257" }, EPSG_4155: { towgs84: "-83,37,124" }, EPSG_4165: { towgs84: "-173,253,27" }, EPSG_4672: { towgs84: "175,-38,113" }, EPSG_4236: { towgs84: "-637,-549,-203" }, EPSG_4251: { towgs84: "-90,40,88" }, EPSG_4271: { towgs84: "-2,374,172" }, EPSG_4175: { towgs84: "-88,4,101" }, EPSG_4716: { towgs84: "298,-304,-375" }, EPSG_4315: { towgs84: "-23,259,-9" }, EPSG_4744: { towgs84: "-242.2,-144.9,370.3" }, EPSG_4244: { towgs84: "-97,787,86" }, EPSG_4293: { towgs84: "616,97,-251" }, EPSG_4714: { towgs84: "-127,-769,472" }, EPSG_4736: { towgs84: "260,12,-147" }, EPSG_6883: { towgs84: "-235,-110,393" }, EPSG_6894: { towgs84: "-63,176,185" }, EPSG_4205: { towgs84: "-43,-163,45" }, EPSG_4256: { towgs84: "41,-220,-134" }, EPSG_4262: { towgs84: "639,405,60" }, EPSG_4604: { towgs84: "174,359,365" }, EPSG_4169: { towgs84: "-115,118,426" }, EPSG_4620: { towgs84: "-106,-129,165" }, EPSG_4184: { towgs84: "-203,141,53" }, EPSG_4616: { towgs84: "-289,-124,60" }, EPSG_9403: { towgs84: "-307,-92,127" }, EPSG_4684: { towgs84: "-133,-321,50" }, EPSG_4708: { towgs84: "-491,-22,435" }, EPSG_4707: { towgs84: "114,-116,-333" }, EPSG_4709: { towgs84: "145,75,-272" }, EPSG_4712: { towgs84: "-205,107,53" }, EPSG_4711: { towgs84: "124,-234,-25" }, EPSG_4718: { towgs84: "230,-199,-752" }, EPSG_4719: { towgs84: "211,147,111" }, EPSG_4724: { towgs84: "208,-435,-229" }, EPSG_4725: { towgs84: "189,-79,-202" }, EPSG_4735: { towgs84: "647,1777,-1124" }, EPSG_4722: { towgs84: "-794,119,-298" }, EPSG_4728: { towgs84: "-307,-92,127" }, EPSG_4734: { towgs84: "-632,438,-609" }, EPSG_4727: { towgs84: "912,-58,1227" }, EPSG_4729: { towgs84: "185,165,42" }, EPSG_4730: { towgs84: "170,42,84" }, EPSG_4733: { towgs84: "276,-57,149" }, ESRI_37218: { towgs84: "230,-199,-752" }, ESRI_37240: { towgs84: "-7,215,225" }, ESRI_37221: { towgs84: "252,-209,-751" }, ESRI_4305: { towgs84: "-123,-206,219" }, ESRI_104139: { towgs84: "-73,-247,227" }, EPSG_4748: { towgs84: "51,391,-36" }, EPSG_4219: { towgs84: "-384,664,-48" }, EPSG_4255: { towgs84: "-333,-222,114" }, EPSG_4257: { towgs84: "-587.8,519.75,145.76" }, EPSG_4646: { towgs84: "-963,510,-359" }, EPSG_6881: { towgs84: "-24,-203,268" }, EPSG_6882: { towgs84: "-183,-15,273" }, EPSG_4715: { towgs84: "-104,-129,239" }, IGNF_RGF93GDD: { towgs84: "0,0,0" }, IGNF_RGM04GDD: { towgs84: "0,0,0" }, IGNF_RGSPM06GDD: { towgs84: "0,0,0" }, IGNF_RGTAAF07GDD: { towgs84: "0,0,0" }, IGNF_RGFG95GDD: { towgs84: "0,0,0" }, IGNF_RGNCG: { towgs84: "0,0,0" }, IGNF_RGPFGDD: { towgs84: "0,0,0" }, IGNF_ETRS89G: { towgs84: "0,0,0" }, IGNF_RGR92GDD: { towgs84: "0,0,0" }, EPSG_4173: { towgs84: "0,0,0" }, EPSG_4180: { towgs84: "0,0,0" }, EPSG_4619: { towgs84: "0,0,0" }, EPSG_4667: { towgs84: "0,0,0" }, EPSG_4075: { towgs84: "0,0,0" }, EPSG_6706: { towgs84: "0,0,0" }, EPSG_7798: { towgs84: "0,0,0" }, EPSG_4661: { towgs84: "0,0,0" }, EPSG_4669: { towgs84: "0,0,0" }, EPSG_8685: { towgs84: "0,0,0" }, EPSG_4151: { towgs84: "0,0,0" }, EPSG_9702: { towgs84: "0,0,0" }, EPSG_4758: { towgs84: "0,0,0" }, EPSG_4761: { towgs84: "0,0,0" }, EPSG_4765: { towgs84: "0,0,0" }, EPSG_8997: { towgs84: "0,0,0" }, EPSG_4023: { towgs84: "0,0,0" }, EPSG_4670: { towgs84: "0,0,0" }, EPSG_4694: { towgs84: "0,0,0" }, EPSG_4148: { towgs84: "0,0,0" }, EPSG_4163: { towgs84: "0,0,0" }, EPSG_4167: { towgs84: "0,0,0" }, EPSG_4189: { towgs84: "0,0,0" }, EPSG_4190: { towgs84: "0,0,0" }, EPSG_4176: { towgs84: "0,0,0" }, EPSG_4659: { towgs84: "0,0,0" }, EPSG_3824: { towgs84: "0,0,0" }, EPSG_3889: { towgs84: "0,0,0" }, EPSG_4046: { towgs84: "0,0,0" }, EPSG_4081: { towgs84: "0,0,0" }, EPSG_4558: { towgs84: "0,0,0" }, EPSG_4483: { towgs84: "0,0,0" }, EPSG_5013: { towgs84: "0,0,0" }, EPSG_5264: { towgs84: "0,0,0" }, EPSG_5324: { towgs84: "0,0,0" }, EPSG_5354: { towgs84: "0,0,0" }, EPSG_5371: { towgs84: "0,0,0" }, EPSG_5373: { towgs84: "0,0,0" }, EPSG_5381: { towgs84: "0,0,0" }, EPSG_5393: { towgs84: "0,0,0" }, EPSG_5489: { towgs84: "0,0,0" }, EPSG_5593: { towgs84: "0,0,0" }, EPSG_6135: { towgs84: "0,0,0" }, EPSG_6365: { towgs84: "0,0,0" }, EPSG_5246: { towgs84: "0,0,0" }, EPSG_7886: { towgs84: "0,0,0" }, EPSG_8431: { towgs84: "0,0,0" }, EPSG_8427: { towgs84: "0,0,0" }, EPSG_8699: { towgs84: "0,0,0" }, EPSG_8818: { towgs84: "0,0,0" }, EPSG_4757: { towgs84: "0,0,0" }, EPSG_9140: { towgs84: "0,0,0" }, EPSG_8086: { towgs84: "0,0,0" }, EPSG_4686: { towgs84: "0,0,0" }, EPSG_4737: { towgs84: "0,0,0" }, EPSG_4702: { towgs84: "0,0,0" }, EPSG_4747: { towgs84: "0,0,0" }, EPSG_4749: { towgs84: "0,0,0" }, EPSG_4674: { towgs84: "0,0,0" }, EPSG_4755: { towgs84: "0,0,0" }, EPSG_4759: { towgs84: "0,0,0" }, EPSG_4762: { towgs84: "0,0,0" }, EPSG_4763: { towgs84: "0,0,0" }, EPSG_4764: { towgs84: "0,0,0" }, EPSG_4166: { towgs84: "0,0,0" }, EPSG_4170: { towgs84: "0,0,0" }, EPSG_5546: { towgs84: "0,0,0" }, EPSG_7844: { towgs84: "0,0,0" }, EPSG_4818: { towgs84: "589,76,480" } };
  for (var Gt in Vt) {
    var kt = Vt[Gt];
    kt.datumName && (Vt[kt.datumName] = kt);
  }
  function qt(t4, a2, o2, h2, c2, u2, d2) {
    var p2 = {};
    return p2.datum_type = void 0 === t4 || "none" === t4 ? n : s, a2 && (p2.datum_params = a2.map(parseFloat), 0 === p2.datum_params[0] && 0 === p2.datum_params[1] && 0 === p2.datum_params[2] || (p2.datum_type = e), p2.datum_params.length > 3 && (0 === p2.datum_params[3] && 0 === p2.datum_params[4] && 0 === p2.datum_params[5] && 0 === p2.datum_params[6] || (p2.datum_type = r, p2.datum_params[3] *= l, p2.datum_params[4] *= l, p2.datum_params[5] *= l, p2.datum_params[6] = p2.datum_params[6] / 1e6 + 1))), d2 && (p2.datum_type = i, p2.grids = d2), p2.a = o2, p2.b = h2, p2.es = c2, p2.ep2 = u2, p2;
  }
  var Bt = {};
  function Ft(t4, e2, r2) {
    return e2 instanceof ArrayBuffer ? Lt(t4, e2, r2) : { ready: jt(t4, e2) };
  }
  function Lt(t4, e2, r2) {
    var i2 = true;
    void 0 !== r2 && false === r2.includeErrorFields && (i2 = false);
    var s2 = new DataView(e2), n2 = Xt(s2), a2 = Qt(s2, n2), o2 = { header: a2, subgrids: Yt(s2, a2, n2, i2) };
    return Bt[t4] = o2, o2;
  }
  async function jt(t4, e2) {
    for (var r2 = [], i2 = await e2.getImageCount(), s2 = i2 - 1; s2 >= 0; s2--) {
      var n2 = await e2.getImage(s2), a2 = await n2.readRasters(), o2 = [n2.getWidth(), n2.getHeight()], h2 = n2.getBoundingBox().map(Wt), l2 = [n2.fileDirectory.ModelPixelScale[0], n2.fileDirectory.ModelPixelScale[1]].map(Wt), c2 = h2[0] + (o2[0] - 1) * l2[0], u2 = h2[3] - (o2[1] - 1) * l2[1], d2 = a2[0], p2 = a2[1], f2 = [];
      for (let t5 = o2[1] - 1; t5 >= 0; t5--)
        for (let e3 = o2[0] - 1; e3 >= 0; e3--) {
          var _2 = t5 * o2[0] + e3;
          f2.push([-Ht(p2[_2]), Ht(d2[_2])]);
        }
      r2.push({ del: l2, lim: o2, ll: [-c2, u2], cvs: f2 });
    }
    var y2 = { header: { nSubgrids: i2 }, subgrids: r2 };
    return Bt[t4] = y2, y2;
  }
  function Dt(t4) {
    return void 0 === t4 ? null : t4.split(",").map(Ut);
  }
  function Ut(t4) {
    if (0 === t4.length)
      return null;
    var e2 = "@" === t4[0];
    return e2 && (t4 = t4.slice(1)), "null" === t4 ? { name: "null", mandatory: !e2, grid: null, isNull: true } : { name: t4, mandatory: !e2, grid: Bt[t4] || null, isNull: false };
  }
  function Wt(t4) {
    return t4 * Math.PI / 180;
  }
  function Ht(t4) {
    return t4 / 3600 * Math.PI / 180;
  }
  function Xt(t4) {
    var e2 = t4.getInt32(8, false);
    return 11 !== e2 && (11 !== (e2 = t4.getInt32(8, true)) && console.warn("Failed to detect nadgrid endian-ness, defaulting to little-endian"), true);
  }
  function Qt(t4, e2) {
    return { nFields: t4.getInt32(8, e2), nSubgridFields: t4.getInt32(24, e2), nSubgrids: t4.getInt32(40, e2), shiftType: Zt(t4, 56, 64).trim(), fromSemiMajorAxis: t4.getFloat64(120, e2), fromSemiMinorAxis: t4.getFloat64(136, e2), toSemiMajorAxis: t4.getFloat64(152, e2), toSemiMinorAxis: t4.getFloat64(168, e2) };
  }
  function Zt(t4, e2, r2) {
    return String.fromCharCode.apply(null, new Uint8Array(t4.buffer.slice(e2, r2)));
  }
  function Yt(t4, e2, r2, i2) {
    for (var s2 = 176, n2 = [], a2 = 0; a2 < e2.nSubgrids; a2++) {
      var o2 = Kt(t4, s2, r2), h2 = te(t4, s2, o2, r2, i2), l2 = Math.round(1 + (o2.upperLongitude - o2.lowerLongitude) / o2.longitudeInterval), c2 = Math.round(1 + (o2.upperLatitude - o2.lowerLatitude) / o2.latitudeInterval);
      n2.push({ ll: [Ht(o2.lowerLongitude), Ht(o2.lowerLatitude)], del: [Ht(o2.longitudeInterval), Ht(o2.latitudeInterval)], lim: [l2, c2], count: o2.gridNodeCount, cvs: Jt(h2) });
      var u2 = 16;
      false === i2 && (u2 = 8), s2 += 176 + o2.gridNodeCount * u2;
    }
    return n2;
  }
  function Jt(t4) {
    return t4.map(function(t5) {
      return [Ht(t5.longitudeShift), Ht(t5.latitudeShift)];
    });
  }
  function Kt(t4, e2, r2) {
    return { name: Zt(t4, e2 + 8, e2 + 16).trim(), parent: Zt(t4, e2 + 24, e2 + 24 + 8).trim(), lowerLatitude: t4.getFloat64(e2 + 72, r2), upperLatitude: t4.getFloat64(e2 + 88, r2), lowerLongitude: t4.getFloat64(e2 + 104, r2), upperLongitude: t4.getFloat64(e2 + 120, r2), latitudeInterval: t4.getFloat64(e2 + 136, r2), longitudeInterval: t4.getFloat64(e2 + 152, r2), gridNodeCount: t4.getInt32(e2 + 168, r2) };
  }
  function te(t4, e2, r2, i2, s2) {
    var n2 = e2 + 176, a2 = 16;
    false === s2 && (a2 = 8);
    for (var o2 = [], h2 = 0; h2 < r2.gridNodeCount; h2++) {
      var l2 = { latitudeShift: t4.getFloat32(n2 + h2 * a2, i2), longitudeShift: t4.getFloat32(n2 + h2 * a2 + 4, i2) };
      false !== s2 && (l2.latitudeAccuracy = t4.getFloat32(n2 + h2 * a2 + 8, i2), l2.longitudeAccuracy = t4.getFloat32(n2 + h2 * a2 + 12, i2)), o2.push(l2);
    }
    return o2;
  }
  function ee(t4, e2) {
    if (!(this instanceof ee))
      return new ee(t4);
    this.forward = null, this.inverse = null, this.init = null, this.name, this.names = null, this.title, e2 = e2 || function(t5) {
      if (t5)
        throw t5;
    };
    var r2 = dt(t4);
    if ("object" == typeof r2) {
      var i2 = ee.projections.get(r2.projName);
      if (i2) {
        if (r2.datumCode && "none" !== r2.datumCode) {
          var s2 = v(Vt, r2.datumCode);
          s2 && (r2.datum_params = r2.datum_params || (s2.towgs84 ? s2.towgs84.split(",") : null), r2.ellps = s2.ellipse, r2.datumName = s2.datumName ? s2.datumName : r2.datumCode);
        }
        r2.k0 = r2.k0 || 1, r2.axis = r2.axis || "enu", r2.ellps = r2.ellps || "wgs84", r2.lat1 = r2.lat1 || r2.lat0;
        var n2 = $t(r2.a, r2.b, r2.rf, r2.ellps, r2.sphere), a2 = Rt(n2.a, n2.b, n2.rf, r2.R_A), o2 = Dt(r2.nadgrids), h2 = r2.datum || qt(r2.datumCode, r2.datum_params, n2.a, n2.b, a2.es, a2.ep2, o2);
        pt(this, r2), pt(this, i2), this.a = n2.a, this.b = n2.b, this.rf = n2.rf, this.sphere = n2.sphere, this.es = a2.es, this.e = a2.e, this.ep2 = a2.ep2, this.datum = h2, "init" in this && "function" == typeof this.init && this.init(), e2(null, this);
      } else
        e2("Could not get projection name from: " + t4);
    } else
      e2("Could not parse to valid json: " + t4);
  }
  function re(t4, i2) {
    return t4.datum_type === i2.datum_type && !(t4.a !== i2.a || Math.abs(t4.es - i2.es) > 5e-11) && (t4.datum_type === e ? t4.datum_params[0] === i2.datum_params[0] && t4.datum_params[1] === i2.datum_params[1] && t4.datum_params[2] === i2.datum_params[2] : t4.datum_type !== r || t4.datum_params[0] === i2.datum_params[0] && t4.datum_params[1] === i2.datum_params[1] && t4.datum_params[2] === i2.datum_params[2] && t4.datum_params[3] === i2.datum_params[3] && t4.datum_params[4] === i2.datum_params[4] && t4.datum_params[5] === i2.datum_params[5] && t4.datum_params[6] === i2.datum_params[6]);
  }
  function ie(t4, e2, r2) {
    var i2, s2, n2, a2, o2 = t4.x, h2 = t4.y, l2 = t4.z ? t4.z : 0;
    if (h2 < -c && h2 > -1.001 * c)
      h2 = -c;
    else if (h2 > c && h2 < 1.001 * c)
      h2 = c;
    else {
      if (h2 < -c)
        return { x: -1 / 0, y: -1 / 0, z: t4.z };
      if (h2 > c)
        return { x: 1 / 0, y: 1 / 0, z: t4.z };
    }
    return o2 > Math.PI && (o2 -= 2 * Math.PI), s2 = Math.sin(h2), a2 = Math.cos(h2), n2 = s2 * s2, { x: ((i2 = r2 / Math.sqrt(1 - e2 * n2)) + l2) * a2 * Math.cos(o2), y: (i2 + l2) * a2 * Math.sin(o2), z: (i2 * (1 - e2) + l2) * s2 };
  }
  function se(t4, e2, r2, i2) {
    var s2, n2, a2, o2, h2, l2, c2, u2, d2, p2, f2, _2, y2, m2, g2, x2 = 1e-12, M2 = x2 * x2, w2 = 30, b2 = t4.x, v2 = t4.y, S2 = t4.z ? t4.z : 0;
    if (s2 = Math.sqrt(b2 * b2 + v2 * v2), n2 = Math.sqrt(b2 * b2 + v2 * v2 + S2 * S2), s2 / r2 < x2) {
      if (m2 = 0, n2 / r2 < x2)
        return g2 = -i2, { x: t4.x, y: t4.y, z: t4.z };
    } else
      m2 = Math.atan2(v2, b2);
    a2 = S2 / n2, u2 = (o2 = s2 / n2) * (1 - e2) * (h2 = 1 / Math.sqrt(1 - e2 * (2 - e2) * o2 * o2)), d2 = a2 * h2, y2 = 0;
    do {
      y2++, l2 = e2 * (c2 = r2 / Math.sqrt(1 - e2 * d2 * d2)) / (c2 + (g2 = s2 * u2 + S2 * d2 - c2 * (1 - e2 * d2 * d2))), _2 = (f2 = a2 * (h2 = 1 / Math.sqrt(1 - l2 * (2 - l2) * o2 * o2))) * u2 - (p2 = o2 * (1 - l2) * h2) * d2, u2 = p2, d2 = f2;
    } while (_2 * _2 > M2 && y2 < w2);
    return { x: m2, y: Math.atan(f2 / Math.abs(p2)), z: g2 };
  }
  function ne(t4, i2, s2) {
    if (i2 === e)
      return { x: t4.x + s2[0], y: t4.y + s2[1], z: t4.z + s2[2] };
    if (i2 === r) {
      var n2 = s2[0], a2 = s2[1], o2 = s2[2], h2 = s2[3], l2 = s2[4], c2 = s2[5], u2 = s2[6];
      return { x: u2 * (t4.x - c2 * t4.y + l2 * t4.z) + n2, y: u2 * (c2 * t4.x + t4.y - h2 * t4.z) + a2, z: u2 * (-l2 * t4.x + h2 * t4.y + t4.z) + o2 };
    }
  }
  function ae(t4, i2, s2) {
    if (i2 === e)
      return { x: t4.x - s2[0], y: t4.y - s2[1], z: t4.z - s2[2] };
    if (i2 === r) {
      var n2 = s2[0], a2 = s2[1], o2 = s2[2], h2 = s2[3], l2 = s2[4], c2 = s2[5], u2 = s2[6], d2 = (t4.x - n2) / u2, p2 = (t4.y - a2) / u2, f2 = (t4.z - o2) / u2;
      return { x: d2 + c2 * p2 - l2 * f2, y: -c2 * d2 + p2 + h2 * f2, z: l2 * d2 - h2 * p2 + f2 };
    }
  }
  function oe(t4) {
    return t4 === e || t4 === r;
  }
  function he(t4, e2, r2) {
    if (re(t4, e2))
      return r2;
    if (t4.datum_type === n || e2.datum_type === n)
      return r2;
    var s2 = t4.a, l2 = t4.es;
    if (t4.datum_type === i) {
      if (0 !== le(t4, false, r2))
        return;
      s2 = a, l2 = h;
    }
    var c2 = e2.a, u2 = e2.b, d2 = e2.es;
    return e2.datum_type === i && (c2 = a, u2 = o, d2 = h), l2 !== d2 || s2 !== c2 || oe(t4.datum_type) || oe(e2.datum_type) ? (r2 = ie(r2, l2, s2), oe(t4.datum_type) && (r2 = ne(r2, t4.datum_type, t4.datum_params)), oe(e2.datum_type) && (r2 = ae(r2, e2.datum_type, e2.datum_params)), r2 = se(r2, d2, c2, u2), e2.datum_type !== i || 0 === le(e2, true, r2) ? r2 : void 0) : r2;
  }
  function le(t4, e2, r2) {
    if (null === t4.grids || 0 === t4.grids.length)
      return console.log("Grid shift grids not found"), -1;
    var i2 = { x: -r2.x, y: r2.y }, s2 = { x: Number.NaN, y: Number.NaN }, n2 = [];
    t:
      for (var a2 = 0; a2 < t4.grids.length; a2++) {
        var o2 = t4.grids[a2];
        if (n2.push(o2.name), o2.isNull) {
          s2 = i2;
          break;
        }
        if (null !== o2.grid)
          for (var h2 = o2.grid.subgrids, l2 = 0, c2 = h2.length; l2 < c2; l2++) {
            var u2 = h2[l2], d2 = (Math.abs(u2.del[1]) + Math.abs(u2.del[0])) / 1e4, p2 = u2.ll[0] - d2, f2 = u2.ll[1] - d2, _2 = u2.ll[0] + (u2.lim[0] - 1) * u2.del[0] + d2, m2 = u2.ll[1] + (u2.lim[1] - 1) * u2.del[1] + d2;
            if (!(f2 > i2.y || p2 > i2.x || m2 < i2.y || _2 < i2.x || (s2 = ce(i2, e2, u2), isNaN(s2.x))))
              break t;
          }
        else if (o2.mandatory)
          return console.log("Unable to find mandatory grid '" + o2.name + "'"), -1;
      }
    return isNaN(s2.x) ? (console.log("Failed to find a grid shift table for location '" + -i2.x * y + " " + i2.y * y + " tried: '" + n2 + "'"), -1) : (r2.x = -s2.x, r2.y = s2.y, 0);
  }
  function ce(t4, e2, r2) {
    var i2 = { x: Number.NaN, y: Number.NaN };
    if (isNaN(t4.x))
      return i2;
    var s2 = { x: t4.x, y: t4.y };
    s2.x -= r2.ll[0], s2.y -= r2.ll[1], s2.x = yt(s2.x - Math.PI) + Math.PI;
    var n2 = ue(s2, r2);
    if (e2) {
      if (isNaN(n2.x))
        return i2;
      n2.x = s2.x - n2.x, n2.y = s2.y - n2.y;
      var a2, o2, h2 = 9, l2 = 1e-12;
      do {
        if (o2 = ue(n2, r2), isNaN(o2.x)) {
          console.log("Inverse grid shift iteration failed, presumably at grid edge.  Using first approximation.");
          break;
        }
        a2 = { x: s2.x - (o2.x + n2.x), y: s2.y - (o2.y + n2.y) }, n2.x += a2.x, n2.y += a2.y;
      } while (h2-- && Math.abs(a2.x) > l2 && Math.abs(a2.y) > l2);
      if (h2 < 0)
        return console.log("Inverse grid shift iterator failed to converge."), i2;
      i2.x = yt(n2.x + r2.ll[0]), i2.y = n2.y + r2.ll[1];
    } else
      isNaN(n2.x) || (i2.x = t4.x + n2.x, i2.y = t4.y + n2.y);
    return i2;
  }
  function ue(t4, e2) {
    var r2, i2 = { x: t4.x / e2.del[0], y: t4.y / e2.del[1] }, s2 = { x: Math.floor(i2.x), y: Math.floor(i2.y) }, n2 = { x: i2.x - 1 * s2.x, y: i2.y - 1 * s2.y }, a2 = { x: Number.NaN, y: Number.NaN };
    if (s2.x < 0 || s2.x >= e2.lim[0])
      return a2;
    if (s2.y < 0 || s2.y >= e2.lim[1])
      return a2;
    r2 = s2.y * e2.lim[0] + s2.x;
    var o2 = { x: e2.cvs[r2][0], y: e2.cvs[r2][1] };
    r2++;
    var h2 = { x: e2.cvs[r2][0], y: e2.cvs[r2][1] };
    r2 += e2.lim[0];
    var l2 = { x: e2.cvs[r2][0], y: e2.cvs[r2][1] };
    r2--;
    var c2 = { x: e2.cvs[r2][0], y: e2.cvs[r2][1] }, u2 = n2.x * n2.y, d2 = n2.x * (1 - n2.y), p2 = (1 - n2.x) * (1 - n2.y), f2 = (1 - n2.x) * n2.y;
    return a2.x = p2 * o2.x + d2 * h2.x + f2 * c2.x + u2 * l2.x, a2.y = p2 * o2.y + d2 * h2.y + f2 * c2.y + u2 * l2.y, a2;
  }
  function de(t4, e2, r2) {
    var i2, s2, n2, a2 = r2.x, o2 = r2.y, h2 = r2.z || 0, l2 = {};
    for (n2 = 0; n2 < 3; n2++)
      if (!e2 || 2 !== n2 || void 0 !== r2.z)
        switch (0 === n2 ? (i2 = a2, s2 = -1 !== "ew".indexOf(t4.axis[n2]) ? "x" : "y") : 1 === n2 ? (i2 = o2, s2 = -1 !== "ns".indexOf(t4.axis[n2]) ? "y" : "x") : (i2 = h2, s2 = "z"), t4.axis[n2]) {
          case "e":
          case "n":
            l2[s2] = i2;
            break;
          case "w":
          case "s":
            l2[s2] = -i2;
            break;
          case "u":
            void 0 !== r2[s2] && (l2.z = i2);
            break;
          case "d":
            void 0 !== r2[s2] && (l2.z = -i2);
            break;
          default:
            return null;
        }
    return l2;
  }
  function pe(t4) {
    var e2 = { x: t4[0], y: t4[1] };
    return t4.length > 2 && (e2.z = t4[2]), t4.length > 3 && (e2.m = t4[3]), e2;
  }
  function fe(t4) {
    _e(t4.x), _e(t4.y);
  }
  function _e(t4) {
    if ("function" == typeof Number.isFinite) {
      if (Number.isFinite(t4))
        return;
      throw new TypeError("coordinates must be finite numbers");
    }
    if ("number" != typeof t4 || t4 != t4 || !isFinite(t4))
      throw new TypeError("coordinates must be finite numbers");
  }
  function ye(t4, s2) {
    return (t4.datum.datum_type === e || t4.datum.datum_type === r || t4.datum.datum_type === i) && "WGS84" !== s2.datumCode || (s2.datum.datum_type === e || s2.datum.datum_type === r || s2.datum.datum_type === i) && "WGS84" !== t4.datumCode;
  }
  function me(t4, e2, r2, i2) {
    var s2, n2 = void 0 !== (r2 = Array.isArray(r2) ? pe(r2) : { x: r2.x, y: r2.y, z: r2.z, m: r2.m }).z;
    if (fe(r2), t4.datum && e2.datum && ye(t4, e2) && (r2 = me(t4, s2 = new ee("WGS84"), r2, i2), t4 = s2), i2 && "enu" !== t4.axis && (r2 = de(t4, false, r2)), "longlat" === t4.projName)
      r2 = { x: r2.x * _, y: r2.y * _, z: r2.z || 0 };
    else if (t4.to_meter && (r2 = { x: r2.x * t4.to_meter, y: r2.y * t4.to_meter, z: r2.z || 0 }), !(r2 = t4.inverse(r2)))
      return;
    if (t4.from_greenwich && (r2.x += t4.from_greenwich), r2 = he(t4.datum, e2.datum, r2))
      return e2.from_greenwich && (r2 = { x: r2.x - e2.from_greenwich, y: r2.y, z: r2.z || 0 }), "longlat" === e2.projName ? r2 = { x: r2.x * y, y: r2.y * y, z: r2.z || 0 } : (r2 = e2.forward(r2), e2.to_meter && (r2 = { x: r2.x / e2.to_meter, y: r2.y / e2.to_meter, z: r2.z || 0 })), i2 && "enu" !== e2.axis ? de(e2, true, r2) : (r2 && !n2 && delete r2.z, r2);
  }
  ee.projections = At2, ee.projections.start();
  var ge = ee("WGS84");
  function xe(t4, e2, r2, i2) {
    var s2, n2, a2;
    return Array.isArray(r2) ? (s2 = me(t4, e2, r2, i2) || { x: NaN, y: NaN }, r2.length > 2 ? void 0 !== t4.name && "geocent" === t4.name || void 0 !== e2.name && "geocent" === e2.name ? "number" == typeof s2.z ? [s2.x, s2.y, s2.z].concat(r2.slice(3)) : [s2.x, s2.y, r2[2]].concat(r2.slice(3)) : [s2.x, s2.y].concat(r2.slice(2)) : [s2.x, s2.y]) : (n2 = me(t4, e2, r2, i2), 2 === (a2 = Object.keys(r2)).length || a2.forEach(function(i3) {
      if (void 0 !== t4.name && "geocent" === t4.name || void 0 !== e2.name && "geocent" === e2.name) {
        if ("x" === i3 || "y" === i3 || "z" === i3)
          return;
      } else if ("x" === i3 || "y" === i3)
        return;
      n2[i3] = r2[i3];
    }), n2);
  }
  function Me(t4) {
    return t4 instanceof ee ? t4 : "object" == typeof t4 && "oProj" in t4 ? t4.oProj : ee(t4);
  }
  function we(t4, e2, r2) {
    var i2, s2, n2, a2 = false;
    return void 0 === e2 ? (s2 = Me(t4), i2 = ge, a2 = true) : (void 0 !== e2.x || Array.isArray(e2)) && (r2 = e2, s2 = Me(t4), i2 = ge, a2 = true), i2 || (i2 = Me(t4)), s2 || (s2 = Me(e2)), r2 ? xe(i2, s2, r2) : (n2 = { forward: function(t5, e3) {
      return xe(i2, s2, t5, e3);
    }, inverse: function(t5, e3) {
      return xe(s2, i2, t5, e3);
    } }, a2 && (n2.oProj = s2), n2);
  }
  var be = 6, ve = "AJSAJS", Se = "AFAFAF", Ce = 65, Ee = 73, Pe = 79, Te = 86, Ie = 90, ze = { forward: Ae, inverse: Oe, toPoint: Ne };
  function Ae(t4, e2) {
    return e2 = e2 || 5, qe(Ve({ lat: t4[1], lon: t4[0] }), e2);
  }
  function Oe(t4) {
    var e2 = Ge(je(t4.toUpperCase()));
    return e2.lat && e2.lon ? [e2.lon, e2.lat, e2.lon, e2.lat] : [e2.left, e2.bottom, e2.right, e2.top];
  }
  function Ne(t4) {
    var e2 = Ge(je(t4.toUpperCase()));
    return e2.lat && e2.lon ? [e2.lon, e2.lat] : [(e2.left + e2.right) / 2, (e2.top + e2.bottom) / 2];
  }
  function Re(t4) {
    return t4 * (Math.PI / 180);
  }
  function $e(t4) {
    return t4 / Math.PI * 180;
  }
  function Ve(t4) {
    var e2, r2, i2, s2, n2, a2, o2, h2 = t4.lat, l2 = t4.lon, c2 = 6378137, u2 = 669438e-8, d2 = 0.9996, p2 = Re(h2), f2 = Re(l2);
    o2 = Math.floor((l2 + 180) / 6) + 1, 180 === l2 && (o2 = 60), h2 >= 56 && h2 < 64 && l2 >= 3 && l2 < 12 && (o2 = 32), h2 >= 72 && h2 < 84 && (l2 >= 0 && l2 < 9 ? o2 = 31 : l2 >= 9 && l2 < 21 ? o2 = 33 : l2 >= 21 && l2 < 33 ? o2 = 35 : l2 >= 33 && l2 < 42 && (o2 = 37)), a2 = Re(6 * (o2 - 1) - 180 + 3), e2 = u2 / (1 - u2), r2 = c2 / Math.sqrt(1 - u2 * Math.sin(p2) * Math.sin(p2)), i2 = Math.tan(p2) * Math.tan(p2), s2 = e2 * Math.cos(p2) * Math.cos(p2);
    var _2 = d2 * r2 * ((n2 = Math.cos(p2) * (f2 - a2)) + (1 - i2 + s2) * n2 * n2 * n2 / 6 + (5 - 18 * i2 + i2 * i2 + 72 * s2 - 58 * e2) * n2 * n2 * n2 * n2 * n2 / 120) + 5e5, y2 = d2 * (c2 * ((1 - u2 / 4 - 3 * u2 * u2 / 64 - 5 * u2 * u2 * u2 / 256) * p2 - (3 * u2 / 8 + 3 * u2 * u2 / 32 + 45 * u2 * u2 * u2 / 1024) * Math.sin(2 * p2) + (15 * u2 * u2 / 256 + 45 * u2 * u2 * u2 / 1024) * Math.sin(4 * p2) - 35 * u2 * u2 * u2 / 3072 * Math.sin(6 * p2)) + r2 * Math.tan(p2) * (n2 * n2 / 2 + (5 - i2 + 9 * s2 + 4 * s2 * s2) * n2 * n2 * n2 * n2 / 24 + (61 - 58 * i2 + i2 * i2 + 600 * s2 - 330 * e2) * n2 * n2 * n2 * n2 * n2 * n2 / 720));
    return h2 < 0 && (y2 += 1e7), { northing: Math.round(y2), easting: Math.round(_2), zoneNumber: o2, zoneLetter: ke(h2) };
  }
  function Ge(t4) {
    var e2 = t4.northing, r2 = t4.easting, i2 = t4.zoneLetter, s2 = t4.zoneNumber;
    if (s2 < 0 || s2 > 60)
      return null;
    var n2, a2, o2, h2, l2, c2, u2, d2, p2, f2 = 0.9996, _2 = 6378137, y2 = 669438e-8, m2 = (1 - Math.sqrt(1 - y2)) / (1 + Math.sqrt(1 - y2)), g2 = r2 - 5e5, x2 = e2;
    i2 < "N" && (x2 -= 1e7), u2 = 6 * (s2 - 1) - 180 + 3, n2 = y2 / (1 - y2), p2 = (d2 = x2 / f2 / (_2 * (1 - y2 / 4 - 3 * y2 * y2 / 64 - 5 * y2 * y2 * y2 / 256))) + (3 * m2 / 2 - 27 * m2 * m2 * m2 / 32) * Math.sin(2 * d2) + (21 * m2 * m2 / 16 - 55 * m2 * m2 * m2 * m2 / 32) * Math.sin(4 * d2) + 151 * m2 * m2 * m2 / 96 * Math.sin(6 * d2), a2 = _2 / Math.sqrt(1 - y2 * Math.sin(p2) * Math.sin(p2)), o2 = Math.tan(p2) * Math.tan(p2), h2 = n2 * Math.cos(p2) * Math.cos(p2), l2 = _2 * (1 - y2) / Math.pow(1 - y2 * Math.sin(p2) * Math.sin(p2), 1.5), c2 = g2 / (a2 * f2);
    var M2 = p2 - a2 * Math.tan(p2) / l2 * (c2 * c2 / 2 - (5 + 3 * o2 + 10 * h2 - 4 * h2 * h2 - 9 * n2) * c2 * c2 * c2 * c2 / 24 + (61 + 90 * o2 + 298 * h2 + 45 * o2 * o2 - 252 * n2 - 3 * h2 * h2) * c2 * c2 * c2 * c2 * c2 * c2 / 720);
    M2 = $e(M2);
    var w2, b2 = (c2 - (1 + 2 * o2 + h2) * c2 * c2 * c2 / 6 + (5 - 2 * h2 + 28 * o2 - 3 * h2 * h2 + 8 * n2 + 24 * o2 * o2) * c2 * c2 * c2 * c2 * c2 / 120) / Math.cos(p2);
    if (b2 = u2 + $e(b2), t4.accuracy) {
      var v2 = Ge({ northing: t4.northing + t4.accuracy, easting: t4.easting + t4.accuracy, zoneLetter: t4.zoneLetter, zoneNumber: t4.zoneNumber });
      w2 = { top: v2.lat, right: v2.lon, bottom: M2, left: b2 };
    } else
      w2 = { lat: M2, lon: b2 };
    return w2;
  }
  function ke(t4) {
    var e2 = "Z";
    return 84 >= t4 && t4 >= 72 ? e2 = "X" : 72 > t4 && t4 >= 64 ? e2 = "W" : 64 > t4 && t4 >= 56 ? e2 = "V" : 56 > t4 && t4 >= 48 ? e2 = "U" : 48 > t4 && t4 >= 40 ? e2 = "T" : 40 > t4 && t4 >= 32 ? e2 = "S" : 32 > t4 && t4 >= 24 ? e2 = "R" : 24 > t4 && t4 >= 16 ? e2 = "Q" : 16 > t4 && t4 >= 8 ? e2 = "P" : 8 > t4 && t4 >= 0 ? e2 = "N" : 0 > t4 && t4 >= -8 ? e2 = "M" : -8 > t4 && t4 >= -16 ? e2 = "L" : -16 > t4 && t4 >= -24 ? e2 = "K" : -24 > t4 && t4 >= -32 ? e2 = "J" : -32 > t4 && t4 >= -40 ? e2 = "H" : -40 > t4 && t4 >= -48 ? e2 = "G" : -48 > t4 && t4 >= -56 ? e2 = "F" : -56 > t4 && t4 >= -64 ? e2 = "E" : -64 > t4 && t4 >= -72 ? e2 = "D" : -72 > t4 && t4 >= -80 && (e2 = "C"), e2;
  }
  function qe(t4, e2) {
    var r2 = "00000" + t4.easting, i2 = "00000" + t4.northing;
    return t4.zoneNumber + t4.zoneLetter + Be(t4.easting, t4.northing, t4.zoneNumber) + r2.substr(r2.length - 5, e2) + i2.substr(i2.length - 5, e2);
  }
  function Be(t4, e2, r2) {
    var i2 = Fe(r2);
    return Le(Math.floor(t4 / 1e5), Math.floor(e2 / 1e5) % 20, i2);
  }
  function Fe(t4) {
    var e2 = t4 % be;
    return 0 === e2 && (e2 = be), e2;
  }
  function Le(t4, e2, r2) {
    var i2 = r2 - 1, s2 = ve.charCodeAt(i2), n2 = Se.charCodeAt(i2), a2 = s2 + t4 - 1, o2 = n2 + e2, h2 = false;
    return a2 > Ie && (a2 = a2 - Ie + Ce - 1, h2 = true), (a2 === Ee || s2 < Ee && a2 > Ee || (a2 > Ee || s2 < Ee) && h2) && a2++, (a2 === Pe || s2 < Pe && a2 > Pe || (a2 > Pe || s2 < Pe) && h2) && ++a2 === Ee && a2++, a2 > Ie && (a2 = a2 - Ie + Ce - 1), o2 > Te ? (o2 = o2 - Te + Ce - 1, h2 = true) : h2 = false, (o2 === Ee || n2 < Ee && o2 > Ee || (o2 > Ee || n2 < Ee) && h2) && o2++, (o2 === Pe || n2 < Pe && o2 > Pe || (o2 > Pe || n2 < Pe) && h2) && ++o2 === Ee && o2++, o2 > Te && (o2 = o2 - Te + Ce - 1), String.fromCharCode(a2) + String.fromCharCode(o2);
  }
  function je(t4) {
    if (t4 && 0 === t4.length)
      throw "MGRSPoint coverting from nothing";
    for (var e2, r2 = t4.length, i2 = null, s2 = "", n2 = 0; !/[A-Z]/.test(e2 = t4.charAt(n2)); ) {
      if (n2 >= 2)
        throw "MGRSPoint bad conversion from: " + t4;
      s2 += e2, n2++;
    }
    var a2 = parseInt(s2, 10);
    if (0 === n2 || n2 + 3 > r2)
      throw "MGRSPoint bad conversion from: " + t4;
    var o2 = t4.charAt(n2++);
    if (o2 <= "A" || "B" === o2 || "Y" === o2 || o2 >= "Z" || "I" === o2 || "O" === o2)
      throw "MGRSPoint zone letter " + o2 + " not handled: " + t4;
    i2 = t4.substring(n2, n2 += 2);
    for (var h2 = Fe(a2), l2 = De(i2.charAt(0), h2), c2 = Ue(i2.charAt(1), h2); c2 < We(o2); )
      c2 += 2e6;
    var u2 = r2 - n2;
    if (u2 % 2 != 0)
      throw "MGRSPoint has to have an even number \nof digits after the zone letter and two 100km letters - front \nhalf for easting meters, second half for \nnorthing meters" + t4;
    var d2, p2, f2, _2 = u2 / 2, y2 = 0, m2 = 0;
    return _2 > 0 && (d2 = 1e5 / Math.pow(10, _2), p2 = t4.substring(n2, n2 + _2), y2 = parseFloat(p2) * d2, f2 = t4.substring(n2 + _2), m2 = parseFloat(f2) * d2), { easting: y2 + l2, northing: m2 + c2, zoneLetter: o2, zoneNumber: a2, accuracy: d2 };
  }
  function De(t4, e2) {
    for (var r2 = ve.charCodeAt(e2 - 1), i2 = 1e5, s2 = false; r2 !== t4.charCodeAt(0); ) {
      if (++r2 === Ee && r2++, r2 === Pe && r2++, r2 > Ie) {
        if (s2)
          throw "Bad character: " + t4;
        r2 = Ce, s2 = true;
      }
      i2 += 1e5;
    }
    return i2;
  }
  function Ue(t4, e2) {
    if (t4 > "V")
      throw "MGRSPoint given invalid Northing " + t4;
    for (var r2 = Se.charCodeAt(e2 - 1), i2 = 0, s2 = false; r2 !== t4.charCodeAt(0); ) {
      if (++r2 === Ee && r2++, r2 === Pe && r2++, r2 > Te) {
        if (s2)
          throw "Bad character: " + t4;
        r2 = Ce, s2 = true;
      }
      i2 += 1e5;
    }
    return i2;
  }
  function We(t4) {
    var e2;
    switch (t4) {
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
    throw "Invalid zone letter: " + t4;
  }
  function He(t4, e2, r2) {
    if (!(this instanceof He))
      return new He(t4, e2, r2);
    if (Array.isArray(t4))
      this.x = t4[0], this.y = t4[1], this.z = t4[2] || 0;
    else if ("object" == typeof t4)
      this.x = t4.x, this.y = t4.y, this.z = t4.z || 0;
    else if ("string" == typeof t4 && void 0 === e2) {
      var i2 = t4.split(",");
      this.x = parseFloat(i2[0]), this.y = parseFloat(i2[1]), this.z = parseFloat(i2[2]) || 0;
    } else
      this.x = t4, this.y = e2, this.z = r2 || 0;
    console.warn("proj4.Point will be removed in version 3, use proj4.toPoint");
  }
  He.fromMGRS = function(t4) {
    return new He(Ne(t4));
  }, He.prototype.toMGRS = function(t4) {
    return Ae([this.x, this.y], t4);
  };
  var Xe = 1, Qe = 0.25, Ze = 0.046875, Ye = 0.01953125, Je = 0.01068115234375, Ke = 0.75, tr = 0.46875, er = 0.013020833333333334, rr = 0.007120768229166667, ir = 0.3645833333333333, sr = 0.005696614583333333, nr = 0.3076171875;
  function ar(t4) {
    var e2 = [];
    e2[0] = Xe - t4 * (Qe + t4 * (Ze + t4 * (Ye + t4 * Je))), e2[1] = t4 * (Ke - t4 * (Ze + t4 * (Ye + t4 * Je)));
    var r2 = t4 * t4;
    return e2[2] = r2 * (tr - t4 * (er + t4 * rr)), r2 *= t4, e2[3] = r2 * (ir - t4 * sr), e2[4] = r2 * t4 * nr, e2;
  }
  function or(t4, e2, r2, i2) {
    return r2 *= e2, e2 *= e2, i2[0] * t4 - r2 * (i2[1] + e2 * (i2[2] + e2 * (i2[3] + e2 * i2[4])));
  }
  var hr = 20;
  function lr(t4, e2, r2) {
    for (var i2 = 1 / (1 - e2), s2 = t4, n2 = hr; n2; --n2) {
      var a2 = Math.sin(s2), o2 = 1 - e2 * a2 * a2;
      if (s2 -= o2 = (or(s2, a2, Math.cos(s2), r2) - t4) * (o2 * Math.sqrt(o2)) * i2, Math.abs(o2) < f)
        return s2;
    }
    return s2;
  }
  function cr() {
    this.x0 = void 0 !== this.x0 ? this.x0 : 0, this.y0 = void 0 !== this.y0 ? this.y0 : 0, this.long0 = void 0 !== this.long0 ? this.long0 : 0, this.lat0 = void 0 !== this.lat0 ? this.lat0 : 0, this.es && (this.en = ar(this.es), this.ml0 = or(this.lat0, Math.sin(this.lat0), Math.cos(this.lat0), this.en));
  }
  function ur(t4) {
    var e2, r2, i2, s2 = t4.x, n2 = t4.y, a2 = yt(s2 - this.long0), o2 = Math.sin(n2), h2 = Math.cos(n2);
    if (this.es) {
      var l2 = h2 * a2, c2 = Math.pow(l2, 2), u2 = this.ep2 * Math.pow(h2, 2), d2 = Math.pow(u2, 2), p2 = Math.abs(h2) > f ? Math.tan(n2) : 0, _2 = Math.pow(p2, 2), y2 = Math.pow(_2, 2);
      e2 = 1 - this.es * Math.pow(o2, 2), l2 /= Math.sqrt(e2);
      var m2 = or(n2, o2, h2, this.en);
      r2 = this.a * (this.k0 * l2 * (1 + c2 / 6 * (1 - _2 + u2 + c2 / 20 * (5 - 18 * _2 + y2 + 14 * u2 - 58 * _2 * u2 + c2 / 42 * (61 + 179 * y2 - y2 * _2 - 479 * _2))))) + this.x0, i2 = this.a * (this.k0 * (m2 - this.ml0 + o2 * a2 * l2 / 2 * (1 + c2 / 12 * (5 - _2 + 9 * u2 + 4 * d2 + c2 / 30 * (61 + y2 - 58 * _2 + 270 * u2 - 330 * _2 * u2 + c2 / 56 * (1385 + 543 * y2 - y2 * _2 - 3111 * _2)))))) + this.y0;
    } else {
      var g2 = h2 * Math.sin(a2);
      if (Math.abs(Math.abs(g2) - 1) < f)
        return 93;
      if (r2 = 0.5 * this.a * this.k0 * Math.log((1 + g2) / (1 - g2)) + this.x0, i2 = h2 * Math.cos(a2) / Math.sqrt(1 - Math.pow(g2, 2)), (g2 = Math.abs(i2)) >= 1) {
        if (g2 - 1 > f)
          return 93;
        i2 = 0;
      } else
        i2 = Math.acos(i2);
      n2 < 0 && (i2 = -i2), i2 = this.a * this.k0 * (i2 - this.lat0) + this.y0;
    }
    return t4.x = r2, t4.y = i2, t4;
  }
  function dr(t4) {
    var e2, r2, i2, s2, n2 = (t4.x - this.x0) * (1 / this.a), a2 = (t4.y - this.y0) * (1 / this.a);
    if (this.es)
      if (r2 = lr(e2 = this.ml0 + a2 / this.k0, this.es, this.en), Math.abs(r2) < c) {
        var o2 = Math.sin(r2), h2 = Math.cos(r2), l2 = Math.abs(h2) > f ? Math.tan(r2) : 0, u2 = this.ep2 * Math.pow(h2, 2), d2 = Math.pow(u2, 2), p2 = Math.pow(l2, 2), _2 = Math.pow(p2, 2);
        e2 = 1 - this.es * Math.pow(o2, 2);
        var y2 = n2 * Math.sqrt(e2) / this.k0, m2 = Math.pow(y2, 2);
        i2 = r2 - (e2 *= l2) * m2 / (1 - this.es) * 0.5 * (1 - m2 / 12 * (5 + 3 * p2 - 9 * u2 * p2 + u2 - 4 * d2 - m2 / 30 * (61 + 90 * p2 - 252 * u2 * p2 + 45 * _2 + 46 * u2 - m2 / 56 * (1385 + 3633 * p2 + 4095 * _2 + 1574 * _2 * p2)))), s2 = yt(this.long0 + y2 * (1 - m2 / 6 * (1 + 2 * p2 + u2 - m2 / 20 * (5 + 28 * p2 + 24 * _2 + 8 * u2 * p2 + 6 * u2 - m2 / 42 * (61 + 662 * p2 + 1320 * _2 + 720 * _2 * p2)))) / h2);
      } else
        i2 = c * _t(a2), s2 = 0;
    else {
      var g2 = Math.exp(n2 / this.k0), x2 = 0.5 * (g2 - 1 / g2), M2 = this.lat0 + a2 / this.k0, w2 = Math.cos(M2);
      e2 = Math.sqrt((1 - Math.pow(w2, 2)) / (1 + Math.pow(x2, 2))), i2 = Math.asin(e2), a2 < 0 && (i2 = -i2), s2 = 0 === x2 && 0 === w2 ? 0 : yt(Math.atan2(x2, w2) + this.long0);
    }
    return t4.x = s2, t4.y = i2, t4;
  }
  var pr = { init: cr, forward: ur, inverse: dr, names: ["Fast_Transverse_Mercator", "Fast Transverse Mercator"] };
  function fr(t4) {
    var e2 = Math.exp(t4);
    return e2 = (e2 - 1 / e2) / 2;
  }
  function _r(t4, e2) {
    t4 = Math.abs(t4), e2 = Math.abs(e2);
    var r2 = Math.max(t4, e2), i2 = Math.min(t4, e2) / (r2 || 1);
    return r2 * Math.sqrt(1 + Math.pow(i2, 2));
  }
  function yr(t4) {
    var e2 = 1 + t4, r2 = e2 - 1;
    return 0 === r2 ? t4 : t4 * Math.log(e2) / r2;
  }
  function mr(t4) {
    var e2 = Math.abs(t4);
    return e2 = yr(e2 * (1 + e2 / (_r(1, e2) + 1))), t4 < 0 ? -e2 : e2;
  }
  function gr(t4, e2) {
    for (var r2, i2 = 2 * Math.cos(2 * e2), s2 = t4.length - 1, n2 = t4[s2], a2 = 0; --s2 >= 0; )
      r2 = i2 * n2 - a2 + t4[s2], a2 = n2, n2 = r2;
    return e2 + r2 * Math.sin(2 * e2);
  }
  function xr(t4, e2) {
    for (var r2, i2 = 2 * Math.cos(e2), s2 = t4.length - 1, n2 = t4[s2], a2 = 0; --s2 >= 0; )
      r2 = i2 * n2 - a2 + t4[s2], a2 = n2, n2 = r2;
    return Math.sin(e2) * r2;
  }
  function Mr(t4) {
    var e2 = Math.exp(t4);
    return e2 = (e2 + 1 / e2) / 2;
  }
  function wr(t4, e2, r2) {
    for (var i2, s2, n2 = Math.sin(e2), a2 = Math.cos(e2), o2 = fr(r2), h2 = Mr(r2), l2 = 2 * a2 * h2, c2 = -2 * n2 * o2, u2 = t4.length - 1, d2 = t4[u2], p2 = 0, f2 = 0, _2 = 0; --u2 >= 0; )
      i2 = f2, s2 = p2, d2 = l2 * (f2 = d2) - i2 - c2 * (p2 = _2) + t4[u2], _2 = c2 * f2 - s2 + l2 * p2;
    return [(l2 = n2 * h2) * d2 - (c2 = a2 * o2) * _2, l2 * _2 + c2 * d2];
  }
  function br() {
    if (!this.approx && (isNaN(this.es) || this.es <= 0))
      throw new Error('Incorrect elliptical usage. Try using the +approx option in the proj string, or PROJECTION["Fast_Transverse_Mercator"] in the WKT.');
    this.approx && (pr.init.apply(this), this.forward = pr.forward, this.inverse = pr.inverse), this.x0 = void 0 !== this.x0 ? this.x0 : 0, this.y0 = void 0 !== this.y0 ? this.y0 : 0, this.long0 = void 0 !== this.long0 ? this.long0 : 0, this.lat0 = void 0 !== this.lat0 ? this.lat0 : 0, this.cgb = [], this.cbg = [], this.utg = [], this.gtu = [];
    var t4 = this.es / (1 + Math.sqrt(1 - this.es)), e2 = t4 / (2 - t4), r2 = e2;
    this.cgb[0] = e2 * (2 + e2 * (-2 / 3 + e2 * (e2 * (116 / 45 + e2 * (26 / 45 + e2 * (-2854 / 675))) - 2))), this.cbg[0] = e2 * (e2 * (2 / 3 + e2 * (4 / 3 + e2 * (-82 / 45 + e2 * (32 / 45 + e2 * (4642 / 4725))))) - 2), r2 *= e2, this.cgb[1] = r2 * (7 / 3 + e2 * (e2 * (-227 / 45 + e2 * (2704 / 315 + e2 * (2323 / 945))) - 1.6)), this.cbg[1] = r2 * (5 / 3 + e2 * (-16 / 15 + e2 * (-13 / 9 + e2 * (904 / 315 + e2 * (-1522 / 945))))), r2 *= e2, this.cgb[2] = r2 * (56 / 15 + e2 * (-136 / 35 + e2 * (-1262 / 105 + e2 * (73814 / 2835)))), this.cbg[2] = r2 * (-26 / 15 + e2 * (34 / 21 + e2 * (1.6 + e2 * (-12686 / 2835)))), r2 *= e2, this.cgb[3] = r2 * (4279 / 630 + e2 * (-332 / 35 + e2 * (-399572 / 14175))), this.cbg[3] = r2 * (1237 / 630 + e2 * (e2 * (-24832 / 14175) - 2.4)), r2 *= e2, this.cgb[4] = r2 * (4174 / 315 + e2 * (-144838 / 6237)), this.cbg[4] = r2 * (-734 / 315 + e2 * (109598 / 31185)), r2 *= e2, this.cgb[5] = r2 * (601676 / 22275), this.cbg[5] = r2 * (444337 / 155925), r2 = Math.pow(e2, 2), this.Qn = this.k0 / (1 + e2) * (1 + r2 * (1 / 4 + r2 * (1 / 64 + r2 / 256))), this.utg[0] = e2 * (e2 * (2 / 3 + e2 * (-37 / 96 + e2 * (1 / 360 + e2 * (81 / 512 + e2 * (-96199 / 604800))))) - 0.5), this.gtu[0] = e2 * (0.5 + e2 * (-2 / 3 + e2 * (5 / 16 + e2 * (41 / 180 + e2 * (-127 / 288 + e2 * (7891 / 37800)))))), this.utg[1] = r2 * (-1 / 48 + e2 * (-1 / 15 + e2 * (437 / 1440 + e2 * (-46 / 105 + e2 * (1118711 / 3870720))))), this.gtu[1] = r2 * (13 / 48 + e2 * (e2 * (557 / 1440 + e2 * (281 / 630 + e2 * (-1983433 / 1935360))) - 0.6)), r2 *= e2, this.utg[2] = r2 * (-17 / 480 + e2 * (37 / 840 + e2 * (209 / 4480 + e2 * (-5569 / 90720)))), this.gtu[2] = r2 * (61 / 240 + e2 * (-103 / 140 + e2 * (15061 / 26880 + e2 * (167603 / 181440)))), r2 *= e2, this.utg[3] = r2 * (-4397 / 161280 + e2 * (11 / 504 + e2 * (830251 / 7257600))), this.gtu[3] = r2 * (49561 / 161280 + e2 * (-179 / 168 + e2 * (6601661 / 7257600))), r2 *= e2, this.utg[4] = r2 * (-4583 / 161280 + e2 * (108847 / 3991680)), this.gtu[4] = r2 * (34729 / 80640 + e2 * (-3418889 / 1995840)), r2 *= e2, this.utg[5] = r2 * (-20648693 / 638668800), this.gtu[5] = 0.6650675310896665 * r2;
    var i2 = gr(this.cbg, this.lat0);
    this.Zb = -this.Qn * (i2 + xr(this.gtu, 2 * i2));
  }
  function vr(t4) {
    var e2 = yt(t4.x - this.long0), r2 = t4.y;
    r2 = gr(this.cbg, r2);
    var i2 = Math.sin(r2), s2 = Math.cos(r2), n2 = Math.sin(e2), a2 = Math.cos(e2);
    r2 = Math.atan2(i2, a2 * s2), e2 = Math.atan2(n2 * s2, _r(i2, s2 * a2)), e2 = mr(Math.tan(e2));
    var o2, h2, l2 = wr(this.gtu, 2 * r2, 2 * e2);
    return r2 += l2[0], e2 += l2[1], Math.abs(e2) <= 2.623395162778 ? (o2 = this.a * (this.Qn * e2) + this.x0, h2 = this.a * (this.Qn * r2 + this.Zb) + this.y0) : (o2 = 1 / 0, h2 = 1 / 0), t4.x = o2, t4.y = h2, t4;
  }
  function Sr(t4) {
    var e2, r2, i2 = (t4.x - this.x0) * (1 / this.a), s2 = (t4.y - this.y0) * (1 / this.a);
    if (s2 = (s2 - this.Zb) / this.Qn, i2 /= this.Qn, Math.abs(i2) <= 2.623395162778) {
      var n2 = wr(this.utg, 2 * s2, 2 * i2);
      s2 += n2[0], i2 += n2[1], i2 = Math.atan(fr(i2));
      var a2 = Math.sin(s2), o2 = Math.cos(s2), h2 = Math.sin(i2), l2 = Math.cos(i2);
      s2 = Math.atan2(a2 * l2, _r(h2, l2 * o2)), e2 = yt((i2 = Math.atan2(h2, l2 * o2)) + this.long0), r2 = gr(this.cgb, s2);
    } else
      e2 = 1 / 0, r2 = 1 / 0;
    return t4.x = e2, t4.y = r2, t4;
  }
  var Cr = { init: br, forward: vr, inverse: Sr, names: ["Extended_Transverse_Mercator", "Extended Transverse Mercator", "etmerc", "Transverse_Mercator", "Transverse Mercator", "Gauss Kruger", "Gauss_Kruger", "tmerc"] };
  function Er(t4, e2) {
    if (void 0 === t4) {
      if ((t4 = Math.floor(30 * (yt(e2) + Math.PI) / Math.PI) + 1) < 0)
        return 0;
      if (t4 > 60)
        return 60;
    }
    return t4;
  }
  function Pr() {
    var t4 = Er(this.zone, this.long0);
    if (void 0 === t4)
      throw new Error("unknown utm zone");
    this.lat0 = 0, this.long0 = (6 * Math.abs(t4) - 183) * _, this.x0 = 5e5, this.y0 = this.utmSouth ? 1e7 : 0, this.k0 = 0.9996, Cr.init.apply(this), this.forward = Cr.forward, this.inverse = Cr.inverse;
  }
  var Tr = { init: Pr, names: ["Universal Transverse Mercator System", "utm"], dependsOn: "etmerc" };
  function Ir(t4, e2) {
    return Math.pow((1 - t4) / (1 + t4), e2);
  }
  var zr = 20;
  function Ar() {
    var t4 = Math.sin(this.lat0), e2 = Math.cos(this.lat0);
    e2 *= e2, this.rc = Math.sqrt(1 - this.es) / (1 - this.es * t4 * t4), this.C = Math.sqrt(1 + this.es * e2 * e2 / (1 - this.es)), this.phic0 = Math.asin(t4 / this.C), this.ratexp = 0.5 * this.C * this.e, this.K = Math.tan(0.5 * this.phic0 + m) / (Math.pow(Math.tan(0.5 * this.lat0 + m), this.C) * Ir(this.e * t4, this.ratexp));
  }
  function Or(t4) {
    var e2 = t4.x, r2 = t4.y;
    return t4.y = 2 * Math.atan(this.K * Math.pow(Math.tan(0.5 * r2 + m), this.C) * Ir(this.e * Math.sin(r2), this.ratexp)) - c, t4.x = this.C * e2, t4;
  }
  function Nr(t4) {
    for (var e2 = 1e-14, r2 = t4.x / this.C, i2 = t4.y, s2 = Math.pow(Math.tan(0.5 * i2 + m) / this.K, 1 / this.C), n2 = zr; n2 > 0 && (i2 = 2 * Math.atan(s2 * Ir(this.e * Math.sin(t4.y), -0.5 * this.e)) - c, !(Math.abs(i2 - t4.y) < e2)); --n2)
      t4.y = i2;
    return n2 ? (t4.x = r2, t4.y = i2, t4) : null;
  }
  var Rr = { init: Ar, forward: Or, inverse: Nr };
  function $r() {
    Rr.init.apply(this), this.rc && (this.sinc0 = Math.sin(this.phic0), this.cosc0 = Math.cos(this.phic0), this.R2 = 2 * this.rc, this.title || (this.title = "Oblique Stereographic Alternative"));
  }
  function Vr(t4) {
    var e2, r2, i2, s2;
    return t4.x = yt(t4.x - this.long0), Rr.forward.apply(this, [t4]), e2 = Math.sin(t4.y), r2 = Math.cos(t4.y), i2 = Math.cos(t4.x), s2 = this.k0 * this.R2 / (1 + this.sinc0 * e2 + this.cosc0 * r2 * i2), t4.x = s2 * r2 * Math.sin(t4.x), t4.y = s2 * (this.cosc0 * e2 - this.sinc0 * r2 * i2), t4.x = this.a * t4.x + this.x0, t4.y = this.a * t4.y + this.y0, t4;
  }
  function Gr(t4) {
    var e2, r2, i2, s2, n2;
    if (t4.x = (t4.x - this.x0) / this.a, t4.y = (t4.y - this.y0) / this.a, t4.x /= this.k0, t4.y /= this.k0, n2 = _r(t4.x, t4.y)) {
      var a2 = 2 * Math.atan2(n2, this.R2);
      e2 = Math.sin(a2), r2 = Math.cos(a2), s2 = Math.asin(r2 * this.sinc0 + t4.y * e2 * this.cosc0 / n2), i2 = Math.atan2(t4.x * e2, n2 * this.cosc0 * r2 - t4.y * this.sinc0 * e2);
    } else
      s2 = this.phic0, i2 = 0;
    return t4.x = i2, t4.y = s2, Rr.inverse.apply(this, [t4]), t4.x = yt(t4.x + this.long0), t4;
  }
  var kr = { init: $r, forward: Vr, inverse: Gr, names: ["Stereographic_North_Pole", "Oblique_Stereographic", "sterea", "Oblique Stereographic Alternative", "Double_Stereographic"] };
  function qr(t4, e2, r2) {
    return e2 *= r2, Math.tan(0.5 * (c + t4)) * Math.pow((1 - e2) / (1 + e2), 0.5 * r2);
  }
  function Br() {
    this.x0 = this.x0 || 0, this.y0 = this.y0 || 0, this.lat0 = this.lat0 || 0, this.long0 = this.long0 || 0, this.coslat0 = Math.cos(this.lat0), this.sinlat0 = Math.sin(this.lat0), this.sphere ? 1 === this.k0 && !isNaN(this.lat_ts) && Math.abs(this.coslat0) <= f && (this.k0 = 0.5 * (1 + _t(this.lat0) * Math.sin(this.lat_ts))) : (Math.abs(this.coslat0) <= f && (this.lat0 > 0 ? this.con = 1 : this.con = -1), this.cons = Math.sqrt(Math.pow(1 + this.e, 1 + this.e) * Math.pow(1 - this.e, 1 - this.e)), 1 === this.k0 && !isNaN(this.lat_ts) && Math.abs(this.coslat0) <= f && Math.abs(Math.cos(this.lat_ts)) > f && (this.k0 = 0.5 * this.cons * ft(this.e, Math.sin(this.lat_ts), Math.cos(this.lat_ts)) / mt(this.e, this.con * this.lat_ts, this.con * Math.sin(this.lat_ts))), this.ms1 = ft(this.e, this.sinlat0, this.coslat0), this.X0 = 2 * Math.atan(qr(this.lat0, this.sinlat0, this.e)) - c, this.cosX0 = Math.cos(this.X0), this.sinX0 = Math.sin(this.X0));
  }
  function Fr(t4) {
    var e2, r2, i2, s2, n2, a2, o2 = t4.x, h2 = t4.y, l2 = Math.sin(h2), u2 = Math.cos(h2), d2 = yt(o2 - this.long0);
    return Math.abs(Math.abs(o2 - this.long0) - Math.PI) <= f && Math.abs(h2 + this.lat0) <= f ? (t4.x = NaN, t4.y = NaN, t4) : this.sphere ? (e2 = 2 * this.k0 / (1 + this.sinlat0 * l2 + this.coslat0 * u2 * Math.cos(d2)), t4.x = this.a * e2 * u2 * Math.sin(d2) + this.x0, t4.y = this.a * e2 * (this.coslat0 * l2 - this.sinlat0 * u2 * Math.cos(d2)) + this.y0, t4) : (r2 = 2 * Math.atan(qr(h2, l2, this.e)) - c, s2 = Math.cos(r2), i2 = Math.sin(r2), Math.abs(this.coslat0) <= f ? (n2 = mt(this.e, h2 * this.con, this.con * l2), a2 = 2 * this.a * this.k0 * n2 / this.cons, t4.x = this.x0 + a2 * Math.sin(o2 - this.long0), t4.y = this.y0 - this.con * a2 * Math.cos(o2 - this.long0), t4) : (Math.abs(this.sinlat0) < f ? (e2 = 2 * this.a * this.k0 / (1 + s2 * Math.cos(d2)), t4.y = e2 * i2) : (e2 = 2 * this.a * this.k0 * this.ms1 / (this.cosX0 * (1 + this.sinX0 * i2 + this.cosX0 * s2 * Math.cos(d2))), t4.y = e2 * (this.cosX0 * i2 - this.sinX0 * s2 * Math.cos(d2)) + this.y0), t4.x = e2 * s2 * Math.sin(d2) + this.x0, t4));
  }
  function Lr(t4) {
    var e2, r2, i2, s2, n2;
    t4.x -= this.x0, t4.y -= this.y0;
    var a2 = Math.sqrt(t4.x * t4.x + t4.y * t4.y);
    if (this.sphere) {
      var o2 = 2 * Math.atan(a2 / (2 * this.a * this.k0));
      return e2 = this.long0, r2 = this.lat0, a2 <= f ? (t4.x = e2, t4.y = r2, t4) : (r2 = Math.asin(Math.cos(o2) * this.sinlat0 + t4.y * Math.sin(o2) * this.coslat0 / a2), e2 = Math.abs(this.coslat0) < f ? this.lat0 > 0 ? yt(this.long0 + Math.atan2(t4.x, -1 * t4.y)) : yt(this.long0 + Math.atan2(t4.x, t4.y)) : yt(this.long0 + Math.atan2(t4.x * Math.sin(o2), a2 * this.coslat0 * Math.cos(o2) - t4.y * this.sinlat0 * Math.sin(o2))), t4.x = e2, t4.y = r2, t4);
    }
    if (Math.abs(this.coslat0) <= f) {
      if (a2 <= f)
        return r2 = this.lat0, e2 = this.long0, t4.x = e2, t4.y = r2, t4;
      t4.x *= this.con, t4.y *= this.con, i2 = a2 * this.cons / (2 * this.a * this.k0), r2 = this.con * gt2(this.e, i2), e2 = this.con * yt(this.con * this.long0 + Math.atan2(t4.x, -1 * t4.y));
    } else
      s2 = 2 * Math.atan(a2 * this.cosX0 / (2 * this.a * this.k0 * this.ms1)), e2 = this.long0, a2 <= f ? n2 = this.X0 : (n2 = Math.asin(Math.cos(s2) * this.sinX0 + t4.y * Math.sin(s2) * this.cosX0 / a2), e2 = yt(this.long0 + Math.atan2(t4.x * Math.sin(s2), a2 * this.cosX0 * Math.cos(s2) - t4.y * this.sinX0 * Math.sin(s2)))), r2 = -1 * gt2(this.e, Math.tan(0.5 * (c + n2)));
    return t4.x = e2, t4.y = r2, t4;
  }
  var jr = { init: Br, forward: Fr, inverse: Lr, names: ["stere", "Stereographic_South_Pole", "Polar_Stereographic_variant_A", "Polar_Stereographic_variant_B", "Polar_Stereographic"], ssfn_: qr };
  function Dr() {
    var t4 = this.lat0;
    this.lambda0 = this.long0;
    var e2 = Math.sin(t4), r2 = this.a, i2 = 1 / this.rf, s2 = 2 * i2 - Math.pow(i2, 2), n2 = this.e = Math.sqrt(s2);
    this.R = this.k0 * r2 * Math.sqrt(1 - s2) / (1 - s2 * Math.pow(e2, 2)), this.alpha = Math.sqrt(1 + s2 / (1 - s2) * Math.pow(Math.cos(t4), 4)), this.b0 = Math.asin(e2 / this.alpha);
    var a2 = Math.log(Math.tan(Math.PI / 4 + this.b0 / 2)), o2 = Math.log(Math.tan(Math.PI / 4 + t4 / 2)), h2 = Math.log((1 + n2 * e2) / (1 - n2 * e2));
    this.K = a2 - this.alpha * o2 + this.alpha * n2 / 2 * h2;
  }
  function Ur(t4) {
    var e2 = Math.log(Math.tan(Math.PI / 4 - t4.y / 2)), r2 = this.e / 2 * Math.log((1 + this.e * Math.sin(t4.y)) / (1 - this.e * Math.sin(t4.y))), i2 = -this.alpha * (e2 + r2) + this.K, s2 = 2 * (Math.atan(Math.exp(i2)) - Math.PI / 4), n2 = this.alpha * (t4.x - this.lambda0), a2 = Math.atan(Math.sin(n2) / (Math.sin(this.b0) * Math.tan(s2) + Math.cos(this.b0) * Math.cos(n2))), o2 = Math.asin(Math.cos(this.b0) * Math.sin(s2) - Math.sin(this.b0) * Math.cos(s2) * Math.cos(n2));
    return t4.y = this.R / 2 * Math.log((1 + Math.sin(o2)) / (1 - Math.sin(o2))) + this.y0, t4.x = this.R * a2 + this.x0, t4;
  }
  function Wr(t4) {
    for (var e2 = t4.x - this.x0, r2 = t4.y - this.y0, i2 = e2 / this.R, s2 = 2 * (Math.atan(Math.exp(r2 / this.R)) - Math.PI / 4), n2 = Math.asin(Math.cos(this.b0) * Math.sin(s2) + Math.sin(this.b0) * Math.cos(s2) * Math.cos(i2)), a2 = Math.atan(Math.sin(i2) / (Math.cos(this.b0) * Math.cos(i2) - Math.sin(this.b0) * Math.tan(s2))), o2 = this.lambda0 + a2 / this.alpha, h2 = 0, l2 = n2, c2 = -1e3, u2 = 0; Math.abs(l2 - c2) > 1e-7; ) {
      if (++u2 > 20)
        return;
      h2 = 1 / this.alpha * (Math.log(Math.tan(Math.PI / 4 + n2 / 2)) - this.K) + this.e * Math.log(Math.tan(Math.PI / 4 + Math.asin(this.e * Math.sin(l2)) / 2)), c2 = l2, l2 = 2 * Math.atan(Math.exp(h2)) - Math.PI / 2;
    }
    return t4.x = o2, t4.y = l2, t4;
  }
  var Hr = { init: Dr, forward: Ur, inverse: Wr, names: ["somerc"] }, Xr = 1e-7;
  function Qr(t4) {
    var e2 = ["Hotine_Oblique_Mercator", "Hotine_Oblique_Mercator_variant_A", "Hotine_Oblique_Mercator_Azimuth_Natural_Origin"], r2 = "object" == typeof t4.projName ? Object.keys(t4.projName)[0] : t4.projName;
    return "no_uoff" in t4 || "no_off" in t4 || -1 !== e2.indexOf(r2) || -1 !== e2.indexOf(Tt(r2));
  }
  function Zr() {
    var t4, e2, r2, i2, s2, n2, a2, o2, h2, l2, u2, d2 = 0, p2 = 0, _2 = 0, y2 = 0, x2 = 0, M2 = 0, w2 = 0;
    this.no_off = Qr(this), this.no_rot = "no_rot" in this;
    var b2 = false;
    "alpha" in this && (b2 = true);
    var v2 = false;
    if ("rectified_grid_angle" in this && (v2 = true), b2 && (w2 = this.alpha), v2 && (d2 = this.rectified_grid_angle), b2 || v2)
      p2 = this.longc;
    else if (_2 = this.long1, x2 = this.lat1, y2 = this.long2, M2 = this.lat2, Math.abs(x2 - M2) <= Xr || (t4 = Math.abs(x2)) <= Xr || Math.abs(t4 - c) <= Xr || Math.abs(Math.abs(this.lat0) - c) <= Xr || Math.abs(Math.abs(M2) - c) <= Xr)
      throw new Error();
    var S2 = 1 - this.es;
    e2 = Math.sqrt(S2), Math.abs(this.lat0) > f ? (o2 = Math.sin(this.lat0), r2 = Math.cos(this.lat0), t4 = 1 - this.es * o2 * o2, this.B = r2 * r2, this.B = Math.sqrt(1 + this.es * this.B * this.B / S2), this.A = this.B * this.k0 * e2 / t4, (s2 = (i2 = this.B * e2 / (r2 * Math.sqrt(t4))) * i2 - 1) <= 0 ? s2 = 0 : (s2 = Math.sqrt(s2), this.lat0 < 0 && (s2 = -s2)), this.E = s2 += i2, this.E *= Math.pow(mt(this.e, this.lat0, o2), this.B)) : (this.B = 1 / e2, this.A = this.k0, this.E = i2 = s2 = 1), b2 || v2 ? (b2 ? (u2 = Math.asin(Math.sin(w2) / i2), v2 || (d2 = w2)) : (u2 = d2, w2 = Math.asin(i2 * Math.sin(u2))), this.lam0 = p2 - Math.asin(0.5 * (s2 - 1 / s2) * Math.tan(u2)) / this.B) : (n2 = Math.pow(mt(this.e, x2, Math.sin(x2)), this.B), a2 = Math.pow(mt(this.e, M2, Math.sin(M2)), this.B), s2 = this.E / n2, h2 = (a2 - n2) / (a2 + n2), l2 = ((l2 = this.E * this.E) - a2 * n2) / (l2 + a2 * n2), (t4 = _2 - y2) < -Math.PI ? y2 -= g : t4 > Math.PI && (y2 += g), this.lam0 = yt(0.5 * (_2 + y2) - Math.atan(l2 * Math.tan(0.5 * this.B * (_2 - y2)) / h2) / this.B), u2 = Math.atan(2 * Math.sin(this.B * yt(_2 - this.lam0)) / (s2 - 1 / s2)), d2 = w2 = Math.asin(i2 * Math.sin(u2))), this.singam = Math.sin(u2), this.cosgam = Math.cos(u2), this.sinrot = Math.sin(d2), this.cosrot = Math.cos(d2), this.rB = 1 / this.B, this.ArB = this.A * this.rB, this.BrA = 1 / this.ArB, this.no_off ? this.u_0 = 0 : (this.u_0 = Math.abs(this.ArB * Math.atan(Math.sqrt(i2 * i2 - 1) / Math.cos(w2))), this.lat0 < 0 && (this.u_0 = -this.u_0)), s2 = 0.5 * u2, this.v_pole_n = this.ArB * Math.log(Math.tan(m - s2)), this.v_pole_s = this.ArB * Math.log(Math.tan(m + s2));
  }
  function Yr(t4) {
    var e2, r2, i2, s2, n2, a2, o2, h2, l2 = {};
    if (t4.x = t4.x - this.lam0, Math.abs(Math.abs(t4.y) - c) > f) {
      if (e2 = 0.5 * ((n2 = this.E / Math.pow(mt(this.e, t4.y, Math.sin(t4.y)), this.B)) - (a2 = 1 / n2)), r2 = 0.5 * (n2 + a2), s2 = Math.sin(this.B * t4.x), i2 = (e2 * this.singam - s2 * this.cosgam) / r2, Math.abs(Math.abs(i2) - 1) < f)
        throw new Error();
      h2 = 0.5 * this.ArB * Math.log((1 - i2) / (1 + i2)), a2 = Math.cos(this.B * t4.x), o2 = Math.abs(a2) < Xr ? this.A * t4.x : this.ArB * Math.atan2(e2 * this.cosgam + s2 * this.singam, a2);
    } else
      h2 = t4.y > 0 ? this.v_pole_n : this.v_pole_s, o2 = this.ArB * t4.y;
    return this.no_rot ? (l2.x = o2, l2.y = h2) : (o2 -= this.u_0, l2.x = h2 * this.cosrot + o2 * this.sinrot, l2.y = o2 * this.cosrot - h2 * this.sinrot), l2.x = this.a * l2.x + this.x0, l2.y = this.a * l2.y + this.y0, l2;
  }
  function Jr(t4) {
    var e2, r2, i2, s2, n2, a2, o2, h2 = {};
    if (t4.x = (t4.x - this.x0) * (1 / this.a), t4.y = (t4.y - this.y0) * (1 / this.a), this.no_rot ? (r2 = t4.y, e2 = t4.x) : (r2 = t4.x * this.cosrot - t4.y * this.sinrot, e2 = t4.y * this.cosrot + t4.x * this.sinrot + this.u_0), s2 = 0.5 * ((i2 = Math.exp(-this.BrA * r2)) - 1 / i2), n2 = 0.5 * (i2 + 1 / i2), o2 = ((a2 = Math.sin(this.BrA * e2)) * this.cosgam + s2 * this.singam) / n2, Math.abs(Math.abs(o2) - 1) < f)
      h2.x = 0, h2.y = o2 < 0 ? -c : c;
    else {
      if (h2.y = this.E / Math.sqrt((1 + o2) / (1 - o2)), h2.y = gt2(this.e, Math.pow(h2.y, 1 / this.B)), h2.y === 1 / 0)
        throw new Error();
      h2.x = -this.rB * Math.atan2(s2 * this.cosgam - a2 * this.singam, Math.cos(this.BrA * e2));
    }
    return h2.x += this.lam0, h2;
  }
  var Kr = { init: Zr, forward: Yr, inverse: Jr, names: ["Hotine_Oblique_Mercator", "Hotine Oblique Mercator", "Hotine_Oblique_Mercator_variant_A", "Hotine_Oblique_Mercator_Variant_B", "Hotine_Oblique_Mercator_Azimuth_Natural_Origin", "Hotine_Oblique_Mercator_Two_Point_Natural_Origin", "Hotine_Oblique_Mercator_Azimuth_Center", "Oblique_Mercator", "omerc"] };
  function ti() {
    if (this.lat2 || (this.lat2 = this.lat1), this.k0 || (this.k0 = 1), this.x0 = this.x0 || 0, this.y0 = this.y0 || 0, !(Math.abs(this.lat1 + this.lat2) < f)) {
      var t4 = this.b / this.a;
      this.e = Math.sqrt(1 - t4 * t4);
      var e2 = Math.sin(this.lat1), r2 = Math.cos(this.lat1), i2 = ft(this.e, e2, r2), s2 = mt(this.e, this.lat1, e2), n2 = Math.sin(this.lat2), a2 = Math.cos(this.lat2), o2 = ft(this.e, n2, a2), h2 = mt(this.e, this.lat2, n2), l2 = Math.abs(Math.abs(this.lat0) - c) < f ? 0 : mt(this.e, this.lat0, Math.sin(this.lat0));
      Math.abs(this.lat1 - this.lat2) > f ? this.ns = Math.log(i2 / o2) / Math.log(s2 / h2) : this.ns = e2, isNaN(this.ns) && (this.ns = e2), this.f0 = i2 / (this.ns * Math.pow(s2, this.ns)), this.rh = this.a * this.f0 * Math.pow(l2, this.ns), this.title || (this.title = "Lambert Conformal Conic");
    }
  }
  function ei(t4) {
    var e2 = t4.x, r2 = t4.y;
    Math.abs(2 * Math.abs(r2) - Math.PI) <= f && (r2 = _t(r2) * (c - 2 * f));
    var i2, s2, n2 = Math.abs(Math.abs(r2) - c);
    if (n2 > f)
      i2 = mt(this.e, r2, Math.sin(r2)), s2 = this.a * this.f0 * Math.pow(i2, this.ns);
    else {
      if ((n2 = r2 * this.ns) <= 0)
        return null;
      s2 = 0;
    }
    var a2 = this.ns * yt(e2 - this.long0);
    return t4.x = this.k0 * (s2 * Math.sin(a2)) + this.x0, t4.y = this.k0 * (this.rh - s2 * Math.cos(a2)) + this.y0, t4;
  }
  function ri(t4) {
    var e2, r2, i2, s2, n2, a2 = (t4.x - this.x0) / this.k0, o2 = this.rh - (t4.y - this.y0) / this.k0;
    this.ns > 0 ? (e2 = Math.sqrt(a2 * a2 + o2 * o2), r2 = 1) : (e2 = -Math.sqrt(a2 * a2 + o2 * o2), r2 = -1);
    var h2 = 0;
    if (0 !== e2 && (h2 = Math.atan2(r2 * a2, r2 * o2)), 0 !== e2 || this.ns > 0) {
      if (r2 = 1 / this.ns, i2 = Math.pow(e2 / (this.a * this.f0), r2), -9999 === (s2 = gt2(this.e, i2)))
        return null;
    } else
      s2 = -c;
    return n2 = yt(h2 / this.ns + this.long0), t4.x = n2, t4.y = s2, t4;
  }
  var ii = { init: ti, forward: ei, inverse: ri, names: ["Lambert Tangential Conformal Conic Projection", "Lambert_Conformal_Conic", "Lambert_Conformal_Conic_1SP", "Lambert_Conformal_Conic_2SP", "lcc", "Lambert Conic Conformal (1SP)", "Lambert Conic Conformal (2SP)"] };
  function si() {
    this.a = 6377397155e-3, this.es = 0.006674372230614, this.e = Math.sqrt(this.es), this.lat0 || (this.lat0 = 0.863937979737193), this.long0 || (this.long0 = 0.4334234309119251), this.k0 || (this.k0 = 0.9999), this.s45 = 0.785398163397448, this.s90 = 2 * this.s45, this.fi0 = this.lat0, this.e2 = this.es, this.e = Math.sqrt(this.e2), this.alfa = Math.sqrt(1 + this.e2 * Math.pow(Math.cos(this.fi0), 4) / (1 - this.e2)), this.uq = 1.04216856380474, this.u0 = Math.asin(Math.sin(this.fi0) / this.alfa), this.g = Math.pow((1 + this.e * Math.sin(this.fi0)) / (1 - this.e * Math.sin(this.fi0)), this.alfa * this.e / 2), this.k = Math.tan(this.u0 / 2 + this.s45) / Math.pow(Math.tan(this.fi0 / 2 + this.s45), this.alfa) * this.g, this.k1 = this.k0, this.n0 = this.a * Math.sqrt(1 - this.e2) / (1 - this.e2 * Math.pow(Math.sin(this.fi0), 2)), this.s0 = 1.37008346281555, this.n = Math.sin(this.s0), this.ro0 = this.k1 * this.n0 / Math.tan(this.s0), this.ad = this.s90 - this.uq;
  }
  function ni(t4) {
    var e2, r2, i2, s2, n2, a2, o2, h2 = t4.x, l2 = t4.y, c2 = yt(h2 - this.long0);
    return e2 = Math.pow((1 + this.e * Math.sin(l2)) / (1 - this.e * Math.sin(l2)), this.alfa * this.e / 2), r2 = 2 * (Math.atan(this.k * Math.pow(Math.tan(l2 / 2 + this.s45), this.alfa) / e2) - this.s45), i2 = -c2 * this.alfa, s2 = Math.asin(Math.cos(this.ad) * Math.sin(r2) + Math.sin(this.ad) * Math.cos(r2) * Math.cos(i2)), n2 = Math.asin(Math.cos(r2) * Math.sin(i2) / Math.cos(s2)), a2 = this.n * n2, o2 = this.ro0 * Math.pow(Math.tan(this.s0 / 2 + this.s45), this.n) / Math.pow(Math.tan(s2 / 2 + this.s45), this.n), t4.y = o2 * Math.cos(a2) / 1, t4.x = o2 * Math.sin(a2) / 1, this.czech || (t4.y *= -1, t4.x *= -1), t4;
  }
  function ai(t4) {
    var e2, r2, i2, s2, n2, a2, o2, h2 = t4.x;
    t4.x = t4.y, t4.y = h2, this.czech || (t4.y *= -1, t4.x *= -1), n2 = Math.sqrt(t4.x * t4.x + t4.y * t4.y), s2 = Math.atan2(t4.y, t4.x) / Math.sin(this.s0), i2 = 2 * (Math.atan(Math.pow(this.ro0 / n2, 1 / this.n) * Math.tan(this.s0 / 2 + this.s45)) - this.s45), e2 = Math.asin(Math.cos(this.ad) * Math.sin(i2) - Math.sin(this.ad) * Math.cos(i2) * Math.cos(s2)), r2 = Math.asin(Math.cos(i2) * Math.sin(s2) / Math.cos(e2)), t4.x = this.long0 - r2 / this.alfa, a2 = e2, o2 = 0;
    var l2 = 0;
    do {
      t4.y = 2 * (Math.atan(Math.pow(this.k, -1 / this.alfa) * Math.pow(Math.tan(e2 / 2 + this.s45), 1 / this.alfa) * Math.pow((1 + this.e * Math.sin(a2)) / (1 - this.e * Math.sin(a2)), this.e / 2)) - this.s45), Math.abs(a2 - t4.y) < 1e-10 && (o2 = 1), a2 = t4.y, l2 += 1;
    } while (0 === o2 && l2 < 15);
    return l2 >= 15 ? null : t4;
  }
  var oi = { init: si, forward: ni, inverse: ai, names: ["Krovak", "krovak"] };
  function hi(t4, e2, r2, i2, s2) {
    return t4 * s2 - e2 * Math.sin(2 * s2) + r2 * Math.sin(4 * s2) - i2 * Math.sin(6 * s2);
  }
  function li(t4) {
    return 1 - 0.25 * t4 * (1 + t4 / 16 * (3 + 1.25 * t4));
  }
  function ci(t4) {
    return 0.375 * t4 * (1 + 0.25 * t4 * (1 + 0.46875 * t4));
  }
  function ui(t4) {
    return 0.05859375 * t4 * t4 * (1 + 0.75 * t4);
  }
  function di(t4) {
    return t4 * t4 * t4 * (35 / 3072);
  }
  function pi(t4, e2, r2) {
    var i2 = e2 * r2;
    return t4 / Math.sqrt(1 - i2 * i2);
  }
  function fi(t4) {
    return Math.abs(t4) < c ? t4 : t4 - _t(t4) * Math.PI;
  }
  function _i(t4, e2, r2, i2, s2) {
    var n2, a2;
    n2 = t4 / e2;
    for (var o2 = 0; o2 < 15; o2++)
      if (n2 += a2 = (t4 - (e2 * n2 - r2 * Math.sin(2 * n2) + i2 * Math.sin(4 * n2) - s2 * Math.sin(6 * n2))) / (e2 - 2 * r2 * Math.cos(2 * n2) + 4 * i2 * Math.cos(4 * n2) - 6 * s2 * Math.cos(6 * n2)), Math.abs(a2) <= 1e-10)
        return n2;
    return NaN;
  }
  function yi() {
    this.sphere || (this.e0 = li(this.es), this.e1 = ci(this.es), this.e2 = ui(this.es), this.e3 = di(this.es), this.ml0 = this.a * hi(this.e0, this.e1, this.e2, this.e3, this.lat0));
  }
  function mi(t4) {
    var e2, r2, i2 = t4.x, s2 = t4.y;
    if (i2 = yt(i2 - this.long0), this.sphere)
      e2 = this.a * Math.asin(Math.cos(s2) * Math.sin(i2)), r2 = this.a * (Math.atan2(Math.tan(s2), Math.cos(i2)) - this.lat0);
    else {
      var n2 = Math.sin(s2), a2 = Math.cos(s2), o2 = pi(this.a, this.e, n2), h2 = Math.tan(s2) * Math.tan(s2), l2 = i2 * Math.cos(s2), c2 = l2 * l2, u2 = this.es * a2 * a2 / (1 - this.es);
      e2 = o2 * l2 * (1 - c2 * h2 * (1 / 6 - (8 - h2 + 8 * u2) * c2 / 120)), r2 = this.a * hi(this.e0, this.e1, this.e2, this.e3, s2) - this.ml0 + o2 * n2 / a2 * c2 * (0.5 + (5 - h2 + 6 * u2) * c2 / 24);
    }
    return t4.x = e2 + this.x0, t4.y = r2 + this.y0, t4;
  }
  function gi(t4) {
    t4.x -= this.x0, t4.y -= this.y0;
    var e2, r2, i2 = t4.x / this.a, s2 = t4.y / this.a;
    if (this.sphere) {
      var n2 = s2 + this.lat0;
      e2 = Math.asin(Math.sin(n2) * Math.cos(i2)), r2 = Math.atan2(Math.tan(i2), Math.cos(n2));
    } else {
      var a2 = _i(this.ml0 / this.a + s2, this.e0, this.e1, this.e2, this.e3);
      if (Math.abs(Math.abs(a2) - c) <= f)
        return t4.x = this.long0, t4.y = c, s2 < 0 && (t4.y *= -1), t4;
      var o2 = pi(this.a, this.e, Math.sin(a2)), h2 = o2 * o2 * o2 / this.a / this.a * (1 - this.es), l2 = Math.pow(Math.tan(a2), 2), u2 = i2 * this.a / o2, d2 = u2 * u2;
      e2 = a2 - o2 * Math.tan(a2) / h2 * u2 * u2 * (0.5 - (1 + 3 * l2) * u2 * u2 / 24), r2 = u2 * (1 - d2 * (l2 / 3 + (1 + 3 * l2) * l2 * d2 / 15)) / Math.cos(a2);
    }
    return t4.x = yt(r2 + this.long0), t4.y = fi(e2), t4;
  }
  var xi = { init: yi, forward: mi, inverse: gi, names: ["Cassini", "Cassini_Soldner", "cass"] };
  function Mi(t4, e2) {
    var r2;
    return t4 > 1e-7 ? (1 - t4 * t4) * (e2 / (1 - (r2 = t4 * e2) * r2) - 0.5 / t4 * Math.log((1 - r2) / (1 + r2))) : 2 * e2;
  }
  var wi = 1, bi = 2, vi = 3, Si = 4;
  function Ci() {
    var t4, e2 = Math.abs(this.lat0);
    if (Math.abs(e2 - c) < f ? this.mode = this.lat0 < 0 ? wi : bi : Math.abs(e2) < f ? this.mode = vi : this.mode = Si, this.es > 0)
      switch (this.qp = Mi(this.e, 1), this.mmf = 0.5 / (1 - this.es), this.apa = Ri(this.es), this.mode) {
        case bi:
        case wi:
          this.dd = 1;
          break;
        case vi:
          this.rq = Math.sqrt(0.5 * this.qp), this.dd = 1 / this.rq, this.xmf = 1, this.ymf = 0.5 * this.qp;
          break;
        case Si:
          this.rq = Math.sqrt(0.5 * this.qp), t4 = Math.sin(this.lat0), this.sinb1 = Mi(this.e, t4) / this.qp, this.cosb1 = Math.sqrt(1 - this.sinb1 * this.sinb1), this.dd = Math.cos(this.lat0) / (Math.sqrt(1 - this.es * t4 * t4) * this.rq * this.cosb1), this.ymf = (this.xmf = this.rq) / this.dd, this.xmf *= this.dd;
      }
    else
      this.mode === Si && (this.sinph0 = Math.sin(this.lat0), this.cosph0 = Math.cos(this.lat0));
  }
  function Ei(t4) {
    var e2, r2, i2, s2, n2, a2, o2, h2, l2, u2, d2 = t4.x, p2 = t4.y;
    if (d2 = yt(d2 - this.long0), this.sphere) {
      if (n2 = Math.sin(p2), u2 = Math.cos(p2), i2 = Math.cos(d2), this.mode === this.OBLIQ || this.mode === this.EQUIT) {
        if ((r2 = this.mode === this.EQUIT ? 1 + u2 * i2 : 1 + this.sinph0 * n2 + this.cosph0 * u2 * i2) <= f)
          return null;
        e2 = (r2 = Math.sqrt(2 / r2)) * u2 * Math.sin(d2), r2 *= this.mode === this.EQUIT ? n2 : this.cosph0 * n2 - this.sinph0 * u2 * i2;
      } else if (this.mode === this.N_POLE || this.mode === this.S_POLE) {
        if (this.mode === this.N_POLE && (i2 = -i2), Math.abs(p2 + this.lat0) < f)
          return null;
        r2 = m - 0.5 * p2, e2 = (r2 = 2 * (this.mode === this.S_POLE ? Math.cos(r2) : Math.sin(r2))) * Math.sin(d2), r2 *= i2;
      }
    } else {
      switch (o2 = 0, h2 = 0, l2 = 0, i2 = Math.cos(d2), s2 = Math.sin(d2), n2 = Math.sin(p2), a2 = Mi(this.e, n2), this.mode !== this.OBLIQ && this.mode !== this.EQUIT || (o2 = a2 / this.qp, h2 = Math.sqrt(1 - o2 * o2)), this.mode) {
        case this.OBLIQ:
          l2 = 1 + this.sinb1 * o2 + this.cosb1 * h2 * i2;
          break;
        case this.EQUIT:
          l2 = 1 + h2 * i2;
          break;
        case this.N_POLE:
          l2 = c + p2, a2 = this.qp - a2;
          break;
        case this.S_POLE:
          l2 = p2 - c, a2 = this.qp + a2;
      }
      if (Math.abs(l2) < f)
        return null;
      switch (this.mode) {
        case this.OBLIQ:
        case this.EQUIT:
          l2 = Math.sqrt(2 / l2), r2 = this.mode === this.OBLIQ ? this.ymf * l2 * (this.cosb1 * o2 - this.sinb1 * h2 * i2) : (l2 = Math.sqrt(2 / (1 + h2 * i2))) * o2 * this.ymf, e2 = this.xmf * l2 * h2 * s2;
          break;
        case this.N_POLE:
        case this.S_POLE:
          a2 >= 0 ? (e2 = (l2 = Math.sqrt(a2)) * s2, r2 = i2 * (this.mode === this.S_POLE ? l2 : -l2)) : e2 = r2 = 0;
      }
    }
    return t4.x = this.a * e2 + this.x0, t4.y = this.a * r2 + this.y0, t4;
  }
  function Pi(t4) {
    t4.x -= this.x0, t4.y -= this.y0;
    var e2, r2, i2, s2, n2, a2, o2, h2 = t4.x / this.a, l2 = t4.y / this.a;
    if (this.sphere) {
      var u2, d2 = 0, p2 = 0;
      if ((r2 = 0.5 * (u2 = Math.sqrt(h2 * h2 + l2 * l2))) > 1)
        return null;
      switch (r2 = 2 * Math.asin(r2), this.mode !== this.OBLIQ && this.mode !== this.EQUIT || (p2 = Math.sin(r2), d2 = Math.cos(r2)), this.mode) {
        case this.EQUIT:
          r2 = Math.abs(u2) <= f ? 0 : Math.asin(l2 * p2 / u2), h2 *= p2, l2 = d2 * u2;
          break;
        case this.OBLIQ:
          r2 = Math.abs(u2) <= f ? this.lat0 : Math.asin(d2 * this.sinph0 + l2 * p2 * this.cosph0 / u2), h2 *= p2 * this.cosph0, l2 = (d2 - Math.sin(r2) * this.sinph0) * u2;
          break;
        case this.N_POLE:
          l2 = -l2, r2 = c - r2;
          break;
        case this.S_POLE:
          r2 -= c;
      }
      e2 = 0 !== l2 || this.mode !== this.EQUIT && this.mode !== this.OBLIQ ? Math.atan2(h2, l2) : 0;
    } else {
      if (o2 = 0, this.mode === this.OBLIQ || this.mode === this.EQUIT) {
        if (h2 /= this.dd, l2 *= this.dd, (a2 = Math.sqrt(h2 * h2 + l2 * l2)) < f)
          return t4.x = this.long0, t4.y = this.lat0, t4;
        s2 = 2 * Math.asin(0.5 * a2 / this.rq), i2 = Math.cos(s2), h2 *= s2 = Math.sin(s2), this.mode === this.OBLIQ ? (o2 = i2 * this.sinb1 + l2 * s2 * this.cosb1 / a2, n2 = this.qp * o2, l2 = a2 * this.cosb1 * i2 - l2 * this.sinb1 * s2) : (o2 = l2 * s2 / a2, n2 = this.qp * o2, l2 = a2 * i2);
      } else if (this.mode === this.N_POLE || this.mode === this.S_POLE) {
        if (this.mode === this.N_POLE && (l2 = -l2), !(n2 = h2 * h2 + l2 * l2))
          return t4.x = this.long0, t4.y = this.lat0, t4;
        o2 = 1 - n2 / this.qp, this.mode === this.S_POLE && (o2 = -o2);
      }
      e2 = Math.atan2(h2, l2), r2 = $i(Math.asin(o2), this.apa);
    }
    return t4.x = yt(this.long0 + e2), t4.y = r2, t4;
  }
  var Ti = 0.3333333333333333, Ii = 0.17222222222222222, zi = 0.10257936507936508, Ai = 0.06388888888888888, Oi = 0.0664021164021164, Ni = 0.016415012942191543;
  function Ri(t4) {
    var e2, r2 = [];
    return r2[0] = t4 * Ti, e2 = t4 * t4, r2[0] += e2 * Ii, r2[1] = e2 * Ai, e2 *= t4, r2[0] += e2 * zi, r2[1] += e2 * Oi, r2[2] = e2 * Ni, r2;
  }
  function $i(t4, e2) {
    var r2 = t4 + t4;
    return t4 + e2[0] * Math.sin(r2) + e2[1] * Math.sin(r2 + r2) + e2[2] * Math.sin(r2 + r2 + r2);
  }
  var Vi = { init: Ci, forward: Ei, inverse: Pi, names: ["Lambert Azimuthal Equal Area", "Lambert_Azimuthal_Equal_Area", "laea"], S_POLE: wi, N_POLE: bi, EQUIT: vi, OBLIQ: Si };
  function Gi(t4) {
    return Math.abs(t4) > 1 && (t4 = t4 > 1 ? 1 : -1), Math.asin(t4);
  }
  function ki() {
    Math.abs(this.lat1 + this.lat2) < f || (this.temp = this.b / this.a, this.es = 1 - Math.pow(this.temp, 2), this.e3 = Math.sqrt(this.es), this.sin_po = Math.sin(this.lat1), this.cos_po = Math.cos(this.lat1), this.t1 = this.sin_po, this.con = this.sin_po, this.ms1 = ft(this.e3, this.sin_po, this.cos_po), this.qs1 = Mi(this.e3, this.sin_po), this.sin_po = Math.sin(this.lat2), this.cos_po = Math.cos(this.lat2), this.t2 = this.sin_po, this.ms2 = ft(this.e3, this.sin_po, this.cos_po), this.qs2 = Mi(this.e3, this.sin_po), this.sin_po = Math.sin(this.lat0), this.cos_po = Math.cos(this.lat0), this.t3 = this.sin_po, this.qs0 = Mi(this.e3, this.sin_po), Math.abs(this.lat1 - this.lat2) > f ? this.ns0 = (this.ms1 * this.ms1 - this.ms2 * this.ms2) / (this.qs2 - this.qs1) : this.ns0 = this.con, this.c = this.ms1 * this.ms1 + this.ns0 * this.qs1, this.rh = this.a * Math.sqrt(this.c - this.ns0 * this.qs0) / this.ns0);
  }
  function qi(t4) {
    var e2 = t4.x, r2 = t4.y;
    this.sin_phi = Math.sin(r2), this.cos_phi = Math.cos(r2);
    var i2 = Mi(this.e3, this.sin_phi), s2 = this.a * Math.sqrt(this.c - this.ns0 * i2) / this.ns0, n2 = this.ns0 * yt(e2 - this.long0), a2 = s2 * Math.sin(n2) + this.x0, o2 = this.rh - s2 * Math.cos(n2) + this.y0;
    return t4.x = a2, t4.y = o2, t4;
  }
  function Bi(t4) {
    var e2, r2, i2, s2, n2, a2;
    return t4.x -= this.x0, t4.y = this.rh - t4.y + this.y0, this.ns0 >= 0 ? (e2 = Math.sqrt(t4.x * t4.x + t4.y * t4.y), i2 = 1) : (e2 = -Math.sqrt(t4.x * t4.x + t4.y * t4.y), i2 = -1), s2 = 0, 0 !== e2 && (s2 = Math.atan2(i2 * t4.x, i2 * t4.y)), i2 = e2 * this.ns0 / this.a, this.sphere ? a2 = Math.asin((this.c - i2 * i2) / (2 * this.ns0)) : (r2 = (this.c - i2 * i2) / this.ns0, a2 = this.phi1z(this.e3, r2)), n2 = yt(s2 / this.ns0 + this.long0), t4.x = n2, t4.y = a2, t4;
  }
  function Fi(t4, e2) {
    var r2, i2, s2, n2, a2 = Gi(0.5 * e2);
    if (t4 < f)
      return a2;
    for (var o2 = t4 * t4, h2 = 1; h2 <= 25; h2++)
      if (a2 += n2 = 0.5 * (s2 = 1 - (i2 = t4 * (r2 = Math.sin(a2))) * i2) * s2 / Math.cos(a2) * (e2 / (1 - o2) - r2 / s2 + 0.5 / t4 * Math.log((1 - i2) / (1 + i2))), Math.abs(n2) <= 1e-7)
        return a2;
    return null;
  }
  var Li = { init: ki, forward: qi, inverse: Bi, names: ["Albers_Conic_Equal_Area", "Albers_Equal_Area", "Albers", "aea"], phi1z: Fi };
  function ji() {
    this.sin_p14 = Math.sin(this.lat0), this.cos_p14 = Math.cos(this.lat0), this.infinity_dist = 1e3 * this.a, this.rc = 1;
  }
  function Di(t4) {
    var e2, r2, i2, s2, n2, a2, o2, h2, l2 = t4.x, c2 = t4.y;
    return i2 = yt(l2 - this.long0), e2 = Math.sin(c2), r2 = Math.cos(c2), s2 = Math.cos(i2), n2 = 1, (a2 = this.sin_p14 * e2 + this.cos_p14 * r2 * s2) > 0 || Math.abs(a2) <= f ? (o2 = this.x0 + this.a * n2 * r2 * Math.sin(i2) / a2, h2 = this.y0 + this.a * n2 * (this.cos_p14 * e2 - this.sin_p14 * r2 * s2) / a2) : (o2 = this.x0 + this.infinity_dist * r2 * Math.sin(i2), h2 = this.y0 + this.infinity_dist * (this.cos_p14 * e2 - this.sin_p14 * r2 * s2)), t4.x = o2, t4.y = h2, t4;
  }
  function Ui(t4) {
    var e2, r2, i2, s2, n2, a2;
    return t4.x = (t4.x - this.x0) / this.a, t4.y = (t4.y - this.y0) / this.a, t4.x /= this.k0, t4.y /= this.k0, (e2 = Math.sqrt(t4.x * t4.x + t4.y * t4.y)) ? (s2 = Math.atan2(e2, this.rc), r2 = Math.sin(s2), a2 = Gi((i2 = Math.cos(s2)) * this.sin_p14 + t4.y * r2 * this.cos_p14 / e2), n2 = Math.atan2(t4.x * r2, e2 * this.cos_p14 * i2 - t4.y * this.sin_p14 * r2), n2 = yt(this.long0 + n2)) : (a2 = this.phic0, n2 = 0), t4.x = n2, t4.y = a2, t4;
  }
  var Wi = { init: ji, forward: Di, inverse: Ui, names: ["gnom"] };
  function Hi(t4, e2) {
    var r2 = 1 - (1 - t4 * t4) / (2 * t4) * Math.log((1 - t4) / (1 + t4));
    if (Math.abs(Math.abs(e2) - r2) < 1e-6)
      return e2 < 0 ? -1 * c : c;
    for (var i2, s2, n2, a2, o2 = Math.asin(0.5 * e2), h2 = 0; h2 < 30; h2++)
      if (s2 = Math.sin(o2), n2 = Math.cos(o2), a2 = t4 * s2, o2 += i2 = Math.pow(1 - a2 * a2, 2) / (2 * n2) * (e2 / (1 - t4 * t4) - s2 / (1 - a2 * a2) + 0.5 / t4 * Math.log((1 - a2) / (1 + a2))), Math.abs(i2) <= 1e-10)
        return o2;
    return NaN;
  }
  function Xi() {
    this.sphere || (this.k0 = ft(this.e, Math.sin(this.lat_ts), Math.cos(this.lat_ts)));
  }
  function Qi(t4) {
    var e2, r2, i2 = t4.x, s2 = t4.y, n2 = yt(i2 - this.long0);
    if (this.sphere)
      e2 = this.x0 + this.a * n2 * Math.cos(this.lat_ts), r2 = this.y0 + this.a * Math.sin(s2) / Math.cos(this.lat_ts);
    else {
      var a2 = Mi(this.e, Math.sin(s2));
      e2 = this.x0 + this.a * this.k0 * n2, r2 = this.y0 + this.a * a2 * 0.5 / this.k0;
    }
    return t4.x = e2, t4.y = r2, t4;
  }
  function Zi(t4) {
    var e2, r2;
    return t4.x -= this.x0, t4.y -= this.y0, this.sphere ? (e2 = yt(this.long0 + t4.x / this.a / Math.cos(this.lat_ts)), r2 = Math.asin(t4.y / this.a * Math.cos(this.lat_ts))) : (r2 = Hi(this.e, 2 * t4.y * this.k0 / this.a), e2 = yt(this.long0 + t4.x / (this.a * this.k0))), t4.x = e2, t4.y = r2, t4;
  }
  var Yi = { init: Xi, forward: Qi, inverse: Zi, names: ["cea"] };
  function Ji() {
    this.x0 = this.x0 || 0, this.y0 = this.y0 || 0, this.lat0 = this.lat0 || 0, this.long0 = this.long0 || 0, this.lat_ts = this.lat_ts || 0, this.title = this.title || "Equidistant Cylindrical (Plate Carre)", this.rc = Math.cos(this.lat_ts);
  }
  function Ki(t4) {
    var e2 = t4.x, r2 = t4.y, i2 = yt(e2 - this.long0), s2 = fi(r2 - this.lat0);
    return t4.x = this.x0 + this.a * i2 * this.rc, t4.y = this.y0 + this.a * s2, t4;
  }
  function ts(t4) {
    var e2 = t4.x, r2 = t4.y;
    return t4.x = yt(this.long0 + (e2 - this.x0) / (this.a * this.rc)), t4.y = fi(this.lat0 + (r2 - this.y0) / this.a), t4;
  }
  var es = { init: Ji, forward: Ki, inverse: ts, names: ["Equirectangular", "Equidistant_Cylindrical", "Equidistant_Cylindrical_Spherical", "eqc"] }, rs = 20;
  function is() {
    this.temp = this.b / this.a, this.es = 1 - Math.pow(this.temp, 2), this.e = Math.sqrt(this.es), this.e0 = li(this.es), this.e1 = ci(this.es), this.e2 = ui(this.es), this.e3 = di(this.es), this.ml0 = this.a * hi(this.e0, this.e1, this.e2, this.e3, this.lat0);
  }
  function ss(t4) {
    var e2, r2, i2, s2 = t4.x, n2 = t4.y, a2 = yt(s2 - this.long0);
    if (i2 = a2 * Math.sin(n2), this.sphere)
      Math.abs(n2) <= f ? (e2 = this.a * a2, r2 = -1 * this.a * this.lat0) : (e2 = this.a * Math.sin(i2) / Math.tan(n2), r2 = this.a * (fi(n2 - this.lat0) + (1 - Math.cos(i2)) / Math.tan(n2)));
    else if (Math.abs(n2) <= f)
      e2 = this.a * a2, r2 = -1 * this.ml0;
    else {
      var o2 = pi(this.a, this.e, Math.sin(n2)) / Math.tan(n2);
      e2 = o2 * Math.sin(i2), r2 = this.a * hi(this.e0, this.e1, this.e2, this.e3, n2) - this.ml0 + o2 * (1 - Math.cos(i2));
    }
    return t4.x = e2 + this.x0, t4.y = r2 + this.y0, t4;
  }
  function ns(t4) {
    var e2, r2, i2, s2, n2, a2, o2, h2, l2;
    if (i2 = t4.x - this.x0, s2 = t4.y - this.y0, this.sphere)
      if (Math.abs(s2 + this.a * this.lat0) <= f)
        e2 = yt(i2 / this.a + this.long0), r2 = 0;
      else {
        var c2;
        for (a2 = this.lat0 + s2 / this.a, o2 = i2 * i2 / this.a / this.a + a2 * a2, h2 = a2, n2 = rs; n2; --n2)
          if (h2 += l2 = -1 * (a2 * (h2 * (c2 = Math.tan(h2)) + 1) - h2 - 0.5 * (h2 * h2 + o2) * c2) / ((h2 - a2) / c2 - 1), Math.abs(l2) <= f) {
            r2 = h2;
            break;
          }
        e2 = yt(this.long0 + Math.asin(i2 * Math.tan(h2) / this.a) / Math.sin(r2));
      }
    else if (Math.abs(s2 + this.ml0) <= f)
      r2 = 0, e2 = yt(this.long0 + i2 / this.a);
    else {
      var u2, d2, p2, _2, y2;
      for (a2 = (this.ml0 + s2) / this.a, o2 = i2 * i2 / this.a / this.a + a2 * a2, h2 = a2, n2 = rs; n2; --n2)
        if (y2 = this.e * Math.sin(h2), u2 = Math.sqrt(1 - y2 * y2) * Math.tan(h2), d2 = this.a * hi(this.e0, this.e1, this.e2, this.e3, h2), p2 = this.e0 - 2 * this.e1 * Math.cos(2 * h2) + 4 * this.e2 * Math.cos(4 * h2) - 6 * this.e3 * Math.cos(6 * h2), h2 -= l2 = (a2 * (u2 * (_2 = d2 / this.a) + 1) - _2 - 0.5 * u2 * (_2 * _2 + o2)) / (this.es * Math.sin(2 * h2) * (_2 * _2 + o2 - 2 * a2 * _2) / (4 * u2) + (a2 - _2) * (u2 * p2 - 2 / Math.sin(2 * h2)) - p2), Math.abs(l2) <= f) {
          r2 = h2;
          break;
        }
      u2 = Math.sqrt(1 - this.es * Math.pow(Math.sin(r2), 2)) * Math.tan(r2), e2 = yt(this.long0 + Math.asin(i2 * u2 / this.a) / Math.sin(r2));
    }
    return t4.x = e2, t4.y = r2, t4;
  }
  var as = { init: is, forward: ss, inverse: ns, names: ["Polyconic", "American_Polyconic", "poly"] };
  function os() {
    this.A = [], this.A[1] = 0.6399175073, this.A[2] = -0.1358797613, this.A[3] = 0.063294409, this.A[4] = -0.02526853, this.A[5] = 0.0117879, this.A[6] = -55161e-7, this.A[7] = 26906e-7, this.A[8] = -1333e-6, this.A[9] = 67e-5, this.A[10] = -34e-5, this.B_re = [], this.B_im = [], this.B_re[1] = 0.7557853228, this.B_im[1] = 0, this.B_re[2] = 0.249204646, this.B_im[2] = 3371507e-9, this.B_re[3] = -1541739e-9, this.B_im[3] = 0.04105856, this.B_re[4] = -0.10162907, this.B_im[4] = 0.01727609, this.B_re[5] = -0.26623489, this.B_im[5] = -0.36249218, this.B_re[6] = -0.6870983, this.B_im[6] = -1.1651967, this.C_re = [], this.C_im = [], this.C_re[1] = 1.3231270439, this.C_im[1] = 0, this.C_re[2] = -0.577245789, this.C_im[2] = -7809598e-9, this.C_re[3] = 0.508307513, this.C_im[3] = -0.112208952, this.C_re[4] = -0.15094762, this.C_im[4] = 0.18200602, this.C_re[5] = 1.01418179, this.C_im[5] = 1.64497696, this.C_re[6] = 1.9660549, this.C_im[6] = 2.5127645, this.D = [], this.D[1] = 1.5627014243, this.D[2] = 0.5185406398, this.D[3] = -0.03333098, this.D[4] = -0.1052906, this.D[5] = -0.0368594, this.D[6] = 7317e-6, this.D[7] = 0.0122, this.D[8] = 394e-5, this.D[9] = -13e-4;
  }
  function hs(t4) {
    var e2, r2 = t4.x, i2 = t4.y - this.lat0, s2 = r2 - this.long0, n2 = i2 / l * 1e-5, a2 = s2, o2 = 1, h2 = 0;
    for (e2 = 1; e2 <= 10; e2++)
      o2 *= n2, h2 += this.A[e2] * o2;
    var c2, u2 = h2, d2 = a2, p2 = 1, f2 = 0, _2 = 0, y2 = 0;
    for (e2 = 1; e2 <= 6; e2++)
      c2 = f2 * u2 + p2 * d2, p2 = p2 * u2 - f2 * d2, f2 = c2, _2 = _2 + this.B_re[e2] * p2 - this.B_im[e2] * f2, y2 = y2 + this.B_im[e2] * p2 + this.B_re[e2] * f2;
    return t4.x = y2 * this.a + this.x0, t4.y = _2 * this.a + this.y0, t4;
  }
  function ls(t4) {
    var e2, r2, i2 = t4.x, s2 = t4.y, n2 = i2 - this.x0, a2 = (s2 - this.y0) / this.a, o2 = n2 / this.a, h2 = 1, c2 = 0, u2 = 0, d2 = 0;
    for (e2 = 1; e2 <= 6; e2++)
      r2 = c2 * a2 + h2 * o2, h2 = h2 * a2 - c2 * o2, c2 = r2, u2 = u2 + this.C_re[e2] * h2 - this.C_im[e2] * c2, d2 = d2 + this.C_im[e2] * h2 + this.C_re[e2] * c2;
    for (var p2 = 0; p2 < this.iterations; p2++) {
      var f2, _2 = u2, y2 = d2, m2 = a2, g2 = o2;
      for (e2 = 2; e2 <= 6; e2++)
        f2 = y2 * u2 + _2 * d2, _2 = _2 * u2 - y2 * d2, y2 = f2, m2 += (e2 - 1) * (this.B_re[e2] * _2 - this.B_im[e2] * y2), g2 += (e2 - 1) * (this.B_im[e2] * _2 + this.B_re[e2] * y2);
      _2 = 1, y2 = 0;
      var x2 = this.B_re[1], M2 = this.B_im[1];
      for (e2 = 2; e2 <= 6; e2++)
        f2 = y2 * u2 + _2 * d2, _2 = _2 * u2 - y2 * d2, y2 = f2, x2 += e2 * (this.B_re[e2] * _2 - this.B_im[e2] * y2), M2 += e2 * (this.B_im[e2] * _2 + this.B_re[e2] * y2);
      var w2 = x2 * x2 + M2 * M2;
      u2 = (m2 * x2 + g2 * M2) / w2, d2 = (g2 * x2 - m2 * M2) / w2;
    }
    var b2 = u2, v2 = d2, S2 = 1, C2 = 0;
    for (e2 = 1; e2 <= 9; e2++)
      S2 *= b2, C2 += this.D[e2] * S2;
    var E2 = this.lat0 + C2 * l * 1e5, P2 = this.long0 + v2;
    return t4.x = P2, t4.y = E2, t4;
  }
  var cs = { init: os, forward: hs, inverse: ls, names: ["New_Zealand_Map_Grid", "nzmg"] };
  function us() {
  }
  function ds(t4) {
    var e2 = t4.x, r2 = t4.y, i2 = yt(e2 - this.long0), s2 = this.x0 + this.a * i2, n2 = this.y0 + this.a * Math.log(Math.tan(Math.PI / 4 + r2 / 2.5)) * 1.25;
    return t4.x = s2, t4.y = n2, t4;
  }
  function ps(t4) {
    t4.x -= this.x0, t4.y -= this.y0;
    var e2 = yt(this.long0 + t4.x / this.a), r2 = 2.5 * (Math.atan(Math.exp(0.8 * t4.y / this.a)) - Math.PI / 4);
    return t4.x = e2, t4.y = r2, t4;
  }
  var fs = { init: us, forward: ds, inverse: ps, names: ["Miller_Cylindrical", "mill"] }, _s = 20;
  function ys() {
    this.sphere ? (this.n = 1, this.m = 0, this.es = 0, this.C_y = Math.sqrt((this.m + 1) / this.n), this.C_x = this.C_y / (this.m + 1)) : this.en = ar(this.es);
  }
  function ms(t4) {
    var e2, r2, i2 = t4.x, s2 = t4.y;
    if (i2 = yt(i2 - this.long0), this.sphere) {
      if (this.m)
        for (var n2 = this.n * Math.sin(s2), a2 = _s; a2; --a2) {
          var o2 = (this.m * s2 + Math.sin(s2) - n2) / (this.m + Math.cos(s2));
          if (s2 -= o2, Math.abs(o2) < f)
            break;
        }
      else
        s2 = 1 !== this.n ? Math.asin(this.n * Math.sin(s2)) : s2;
      e2 = this.a * this.C_x * i2 * (this.m + Math.cos(s2)), r2 = this.a * this.C_y * s2;
    } else {
      var h2 = Math.sin(s2), l2 = Math.cos(s2);
      r2 = this.a * or(s2, h2, l2, this.en), e2 = this.a * i2 * l2 / Math.sqrt(1 - this.es * h2 * h2);
    }
    return t4.x = e2, t4.y = r2, t4;
  }
  function gs(t4) {
    var e2, r2, i2;
    return t4.x -= this.x0, r2 = t4.x / this.a, t4.y -= this.y0, e2 = t4.y / this.a, this.sphere ? (e2 /= this.C_y, r2 /= this.C_x * (this.m + Math.cos(e2)), this.m ? e2 = Gi((this.m * e2 + Math.sin(e2)) / this.n) : 1 !== this.n && (e2 = Gi(Math.sin(e2) / this.n)), r2 = yt(r2 + this.long0), e2 = fi(e2)) : (e2 = lr(t4.y / this.a, this.es, this.en), (i2 = Math.abs(e2)) < c ? (i2 = Math.sin(e2), r2 = yt(this.long0 + t4.x * Math.sqrt(1 - this.es * i2 * i2) / (this.a * Math.cos(e2)))) : i2 - f < c && (r2 = this.long0)), t4.x = r2, t4.y = e2, t4;
  }
  var xs = { init: ys, forward: ms, inverse: gs, names: ["Sinusoidal", "sinu"] };
  function Ms() {
  }
  function ws(t4) {
    for (var e2 = t4.x, r2 = t4.y, i2 = yt(e2 - this.long0), s2 = r2, n2 = Math.PI * Math.sin(r2); ; ) {
      var a2 = -(s2 + Math.sin(s2) - n2) / (1 + Math.cos(s2));
      if (s2 += a2, Math.abs(a2) < f)
        break;
    }
    s2 /= 2, Math.PI / 2 - Math.abs(r2) < f && (i2 = 0);
    var o2 = 0.900316316158 * this.a * i2 * Math.cos(s2) + this.x0, h2 = 1.4142135623731 * this.a * Math.sin(s2) + this.y0;
    return t4.x = o2, t4.y = h2, t4;
  }
  function bs(t4) {
    var e2, r2;
    t4.x -= this.x0, t4.y -= this.y0, r2 = t4.y / (1.4142135623731 * this.a), Math.abs(r2) > 0.999999999999 && (r2 = 0.999999999999), e2 = Math.asin(r2);
    var i2 = yt(this.long0 + t4.x / (0.900316316158 * this.a * Math.cos(e2)));
    i2 < -Math.PI && (i2 = -Math.PI), i2 > Math.PI && (i2 = Math.PI), r2 = (2 * e2 + Math.sin(2 * e2)) / Math.PI, Math.abs(r2) > 1 && (r2 = 1);
    var s2 = Math.asin(r2);
    return t4.x = i2, t4.y = s2, t4;
  }
  var vs = { init: Ms, forward: ws, inverse: bs, names: ["Mollweide", "moll"] };
  function Ss() {
    Math.abs(this.lat1 + this.lat2) < f || (this.lat2 = this.lat2 || this.lat1, this.temp = this.b / this.a, this.es = 1 - Math.pow(this.temp, 2), this.e = Math.sqrt(this.es), this.e0 = li(this.es), this.e1 = ci(this.es), this.e2 = ui(this.es), this.e3 = di(this.es), this.sin_phi = Math.sin(this.lat1), this.cos_phi = Math.cos(this.lat1), this.ms1 = ft(this.e, this.sin_phi, this.cos_phi), this.ml1 = hi(this.e0, this.e1, this.e2, this.e3, this.lat1), Math.abs(this.lat1 - this.lat2) < f ? this.ns = this.sin_phi : (this.sin_phi = Math.sin(this.lat2), this.cos_phi = Math.cos(this.lat2), this.ms2 = ft(this.e, this.sin_phi, this.cos_phi), this.ml2 = hi(this.e0, this.e1, this.e2, this.e3, this.lat2), this.ns = (this.ms1 - this.ms2) / (this.ml2 - this.ml1)), this.g = this.ml1 + this.ms1 / this.ns, this.ml0 = hi(this.e0, this.e1, this.e2, this.e3, this.lat0), this.rh = this.a * (this.g - this.ml0));
  }
  function Cs(t4) {
    var e2, r2 = t4.x, i2 = t4.y;
    if (this.sphere)
      e2 = this.a * (this.g - i2);
    else {
      var s2 = hi(this.e0, this.e1, this.e2, this.e3, i2);
      e2 = this.a * (this.g - s2);
    }
    var n2 = this.ns * yt(r2 - this.long0), a2 = this.x0 + e2 * Math.sin(n2), o2 = this.y0 + this.rh - e2 * Math.cos(n2);
    return t4.x = a2, t4.y = o2, t4;
  }
  function Es(t4) {
    var e2, r2, i2, s2;
    t4.x -= this.x0, t4.y = this.rh - t4.y + this.y0, this.ns >= 0 ? (r2 = Math.sqrt(t4.x * t4.x + t4.y * t4.y), e2 = 1) : (r2 = -Math.sqrt(t4.x * t4.x + t4.y * t4.y), e2 = -1);
    var n2 = 0;
    return 0 !== r2 && (n2 = Math.atan2(e2 * t4.x, e2 * t4.y)), this.sphere ? (s2 = yt(this.long0 + n2 / this.ns), i2 = fi(this.g - r2 / this.a), t4.x = s2, t4.y = i2, t4) : (i2 = _i(this.g - r2 / this.a, this.e0, this.e1, this.e2, this.e3), s2 = yt(this.long0 + n2 / this.ns), t4.x = s2, t4.y = i2, t4);
  }
  var Ps = { init: Ss, forward: Cs, inverse: Es, names: ["Equidistant_Conic", "eqdc"] };
  function Ts() {
    this.R = this.a;
  }
  function Is(t4) {
    var e2, r2, i2 = t4.x, s2 = t4.y, n2 = yt(i2 - this.long0);
    Math.abs(s2) <= f && (e2 = this.x0 + this.R * n2, r2 = this.y0);
    var a2 = Gi(2 * Math.abs(s2 / Math.PI));
    (Math.abs(n2) <= f || Math.abs(Math.abs(s2) - c) <= f) && (e2 = this.x0, r2 = s2 >= 0 ? this.y0 + Math.PI * this.R * Math.tan(0.5 * a2) : this.y0 + Math.PI * this.R * -Math.tan(0.5 * a2));
    var o2 = 0.5 * Math.abs(Math.PI / n2 - n2 / Math.PI), h2 = o2 * o2, l2 = Math.sin(a2), u2 = Math.cos(a2), d2 = u2 / (l2 + u2 - 1), p2 = d2 * d2, _2 = d2 * (2 / l2 - 1), y2 = _2 * _2, m2 = Math.PI * this.R * (o2 * (d2 - y2) + Math.sqrt(h2 * (d2 - y2) * (d2 - y2) - (y2 + h2) * (p2 - y2))) / (y2 + h2);
    n2 < 0 && (m2 = -m2), e2 = this.x0 + m2;
    var g2 = h2 + d2;
    return m2 = Math.PI * this.R * (_2 * g2 - o2 * Math.sqrt((y2 + h2) * (h2 + 1) - g2 * g2)) / (y2 + h2), r2 = s2 >= 0 ? this.y0 + m2 : this.y0 - m2, t4.x = e2, t4.y = r2, t4;
  }
  function zs(t4) {
    var e2, r2, i2, s2, n2, a2, o2, h2, l2, c2, u2, d2;
    return t4.x -= this.x0, t4.y -= this.y0, u2 = Math.PI * this.R, n2 = (i2 = t4.x / u2) * i2 + (s2 = t4.y / u2) * s2, u2 = 3 * (s2 * s2 / (h2 = -2 * (a2 = -Math.abs(s2) * (1 + n2)) + 1 + 2 * s2 * s2 + n2 * n2) + (2 * (o2 = a2 - 2 * s2 * s2 + i2 * i2) * o2 * o2 / h2 / h2 / h2 - 9 * a2 * o2 / h2 / h2) / 27) / (l2 = (a2 - o2 * o2 / 3 / h2) / h2) / (c2 = 2 * Math.sqrt(-l2 / 3)), Math.abs(u2) > 1 && (u2 = u2 >= 0 ? 1 : -1), d2 = Math.acos(u2) / 3, r2 = t4.y >= 0 ? (-c2 * Math.cos(d2 + Math.PI / 3) - o2 / 3 / h2) * Math.PI : -(-c2 * Math.cos(d2 + Math.PI / 3) - o2 / 3 / h2) * Math.PI, e2 = Math.abs(i2) < f ? this.long0 : yt(this.long0 + Math.PI * (n2 - 1 + Math.sqrt(1 + 2 * (i2 * i2 - s2 * s2) + n2 * n2)) / 2 / i2), t4.x = e2, t4.y = r2, t4;
  }
  var As = { init: Ts, forward: Is, inverse: zs, names: ["Van_der_Grinten_I", "VanDerGrinten", "Van_der_Grinten", "vandg"] };
  function Os(t4, e2, r2, i2, s2, n2) {
    const a2 = i2 - e2, o2 = Math.atan((1 - n2) * Math.tan(t4)), h2 = Math.atan((1 - n2) * Math.tan(r2)), l2 = Math.sin(o2), c2 = Math.cos(o2), u2 = Math.sin(h2), d2 = Math.cos(h2);
    let p2, f2, _2, y2, m2, g2, x2, M2, w2, b2, v2, S2, C2, E2, P2, T2 = a2, I2 = 100;
    do {
      if (f2 = Math.sin(T2), _2 = Math.cos(T2), y2 = Math.sqrt(d2 * f2 * (d2 * f2) + (c2 * u2 - l2 * d2 * _2) * (c2 * u2 - l2 * d2 * _2)), 0 === y2)
        return { azi1: 0, s12: 0 };
      m2 = l2 * u2 + c2 * d2 * _2, g2 = Math.atan2(y2, m2), x2 = c2 * d2 * f2 / y2, M2 = 1 - x2 * x2, w2 = 0 !== M2 ? m2 - 2 * l2 * u2 / M2 : 0, b2 = n2 / 16 * M2 * (4 + n2 * (4 - 3 * M2)), p2 = T2, T2 = a2 + (1 - b2) * n2 * x2 * (g2 + b2 * y2 * (w2 + b2 * m2 * (2 * w2 * w2 - 1)));
    } while (Math.abs(T2 - p2) > 1e-12 && --I2 > 0);
    return 0 === I2 ? { azi1: NaN, s12: NaN } : (v2 = M2 * (s2 * s2 - s2 * (1 - n2) * (s2 * (1 - n2))) / (s2 * (1 - n2) * (s2 * (1 - n2))), S2 = 1 + v2 / 16384 * (4096 + v2 * (v2 * (320 - 175 * v2) - 768)), C2 = v2 / 1024 * (256 + v2 * (v2 * (74 - 47 * v2) - 128)), E2 = C2 * y2 * (w2 + C2 / 4 * (m2 * (2 * w2 * w2 - 1) - C2 / 6 * w2 * (4 * y2 * y2 - 3) * (4 * w2 * w2 - 3))), P2 = s2 * (1 - n2) * S2 * (g2 - E2), { azi1: Math.atan2(d2 * f2, c2 * u2 - l2 * d2 * _2), s12: P2 });
  }
  function Ns(t4, e2, r2, i2, s2, n2) {
    const a2 = Math.atan((1 - n2) * Math.tan(t4)), o2 = Math.sin(a2), h2 = Math.cos(a2), l2 = Math.sin(r2), c2 = Math.cos(r2), u2 = Math.atan2(o2, h2 * c2), d2 = h2 * l2, p2 = 1 - d2 * d2, f2 = p2 * (s2 * s2 - s2 * (1 - n2) * (s2 * (1 - n2))) / (s2 * (1 - n2) * (s2 * (1 - n2))), _2 = 1 + f2 / 16384 * (4096 + f2 * (f2 * (320 - 175 * f2) - 768)), y2 = f2 / 1024 * (256 + f2 * (f2 * (74 - 47 * f2) - 128));
    let m2, g2, x2, M2, w2, b2 = i2 / (s2 * (1 - n2) * _2), v2 = 100;
    do {
      g2 = Math.cos(2 * u2 + b2), x2 = Math.sin(b2), M2 = Math.cos(b2), w2 = y2 * x2 * (g2 + y2 / 4 * (M2 * (2 * g2 * g2 - 1) - y2 / 6 * g2 * (4 * x2 * x2 - 3) * (4 * g2 * g2 - 3))), m2 = b2, b2 = i2 / (s2 * (1 - n2) * _2) + w2;
    } while (Math.abs(b2 - m2) > 1e-12 && --v2 > 0);
    if (0 === v2)
      return { lat2: NaN, lon2: NaN };
    const S2 = o2 * x2 - h2 * M2 * c2, C2 = n2 / 16 * p2 * (4 + n2 * (4 - 3 * p2));
    return { lat2: Math.atan2(o2 * M2 + h2 * x2 * c2, (1 - n2) * Math.sqrt(d2 * d2 + S2 * S2)), lon2: e2 + (Math.atan2(x2 * l2, h2 * M2 - o2 * x2 * c2) - (1 - C2) * n2 * d2 * (b2 + C2 * x2 * (g2 + C2 * M2 * (2 * g2 * g2 - 1)))) };
  }
  function Rs() {
    this.sin_p12 = Math.sin(this.lat0), this.cos_p12 = Math.cos(this.lat0), this.f = this.es / (1 + Math.sqrt(1 - this.es));
  }
  function $s(t4) {
    var e2, r2, i2, s2, n2, a2, o2, h2, l2, u2, d2, p2 = t4.x, _2 = t4.y, y2 = Math.sin(t4.y), m2 = Math.cos(t4.y), g2 = yt(p2 - this.long0);
    return this.sphere ? Math.abs(this.sin_p12 - 1) <= f ? (t4.x = this.x0 + this.a * (c - _2) * Math.sin(g2), t4.y = this.y0 - this.a * (c - _2) * Math.cos(g2), t4) : Math.abs(this.sin_p12 + 1) <= f ? (t4.x = this.x0 + this.a * (c + _2) * Math.sin(g2), t4.y = this.y0 + this.a * (c + _2) * Math.cos(g2), t4) : (l2 = this.sin_p12 * y2 + this.cos_p12 * m2 * Math.cos(g2), h2 = (o2 = Math.acos(l2)) ? o2 / Math.sin(o2) : 1, t4.x = this.x0 + this.a * h2 * m2 * Math.sin(g2), t4.y = this.y0 + this.a * h2 * (this.cos_p12 * y2 - this.sin_p12 * m2 * Math.cos(g2)), t4) : (e2 = li(this.es), r2 = ci(this.es), i2 = ui(this.es), s2 = di(this.es), Math.abs(this.sin_p12 - 1) <= f ? (n2 = this.a * hi(e2, r2, i2, s2, c), a2 = this.a * hi(e2, r2, i2, s2, _2), t4.x = this.x0 + (n2 - a2) * Math.sin(g2), t4.y = this.y0 - (n2 - a2) * Math.cos(g2), t4) : Math.abs(this.sin_p12 + 1) <= f ? (n2 = this.a * hi(e2, r2, i2, s2, c), a2 = this.a * hi(e2, r2, i2, s2, _2), t4.x = this.x0 + (n2 + a2) * Math.sin(g2), t4.y = this.y0 + (n2 + a2) * Math.cos(g2), t4) : Math.abs(p2) < f && Math.abs(_2 - this.lat0) < f ? (t4.x = t4.y = 0, t4) : (d2 = (u2 = Os(this.lat0, this.long0, _2, p2, this.a, this.f)).azi1, t4.x = u2.s12 * Math.sin(d2), t4.y = u2.s12 * Math.cos(d2), t4));
  }
  function Vs(t4) {
    var e2, r2, i2, s2, n2, a2, o2, h2, l2, u2, d2, p2, _2, y2, m2;
    if (t4.x -= this.x0, t4.y -= this.y0, this.sphere) {
      if ((e2 = Math.sqrt(t4.x * t4.x + t4.y * t4.y)) > 2 * c * this.a)
        return;
      return r2 = e2 / this.a, i2 = Math.sin(r2), s2 = Math.cos(r2), n2 = this.long0, Math.abs(e2) <= f ? a2 = this.lat0 : (a2 = Gi(s2 * this.sin_p12 + t4.y * i2 * this.cos_p12 / e2), o2 = Math.abs(this.lat0) - c, n2 = Math.abs(o2) <= f ? this.lat0 >= 0 ? yt(this.long0 + Math.atan2(t4.x, -t4.y)) : yt(this.long0 - Math.atan2(-t4.x, t4.y)) : yt(this.long0 + Math.atan2(t4.x * i2, e2 * this.cos_p12 * s2 - t4.y * this.sin_p12 * i2))), t4.x = n2, t4.y = a2, t4;
    }
    return h2 = li(this.es), l2 = ci(this.es), u2 = ui(this.es), d2 = di(this.es), Math.abs(this.sin_p12 - 1) <= f ? (a2 = _i(((p2 = this.a * hi(h2, l2, u2, d2, c)) - (e2 = Math.sqrt(t4.x * t4.x + t4.y * t4.y))) / this.a, h2, l2, u2, d2), n2 = yt(this.long0 + Math.atan2(t4.x, -1 * t4.y)), t4.x = n2, t4.y = a2, t4) : Math.abs(this.sin_p12 + 1) <= f ? (p2 = this.a * hi(h2, l2, u2, d2, c), a2 = _i(((e2 = Math.sqrt(t4.x * t4.x + t4.y * t4.y)) - p2) / this.a, h2, l2, u2, d2), n2 = yt(this.long0 + Math.atan2(t4.x, t4.y)), t4.x = n2, t4.y = a2, t4) : (_2 = Math.atan2(t4.x, t4.y), y2 = Math.sqrt(t4.x * t4.x + t4.y * t4.y), m2 = Ns(this.lat0, this.long0, _2, y2, this.a, this.f), t4.x = m2.lon2, t4.y = m2.lat2, t4);
  }
  var Gs = { init: Rs, forward: $s, inverse: Vs, names: ["Azimuthal_Equidistant", "aeqd"] };
  function ks() {
    this.sin_p14 = Math.sin(this.lat0), this.cos_p14 = Math.cos(this.lat0);
  }
  function qs(t4) {
    var e2, r2, i2, s2, n2, a2, o2, h2, l2 = t4.x, c2 = t4.y;
    return i2 = yt(l2 - this.long0), e2 = Math.sin(c2), r2 = Math.cos(c2), s2 = Math.cos(i2), n2 = 1, ((a2 = this.sin_p14 * e2 + this.cos_p14 * r2 * s2) > 0 || Math.abs(a2) <= f) && (o2 = this.a * n2 * r2 * Math.sin(i2), h2 = this.y0 + this.a * n2 * (this.cos_p14 * e2 - this.sin_p14 * r2 * s2)), t4.x = o2, t4.y = h2, t4;
  }
  function Bs(t4) {
    var e2, r2, i2, s2, n2, a2, o2;
    return t4.x -= this.x0, t4.y -= this.y0, r2 = Gi((e2 = Math.sqrt(t4.x * t4.x + t4.y * t4.y)) / this.a), i2 = Math.sin(r2), s2 = Math.cos(r2), a2 = this.long0, Math.abs(e2) <= f ? (o2 = this.lat0, t4.x = a2, t4.y = o2, t4) : (o2 = Gi(s2 * this.sin_p14 + t4.y * i2 * this.cos_p14 / e2), n2 = Math.abs(this.lat0) - c, Math.abs(n2) <= f ? (a2 = this.lat0 >= 0 ? yt(this.long0 + Math.atan2(t4.x, -t4.y)) : yt(this.long0 - Math.atan2(-t4.x, t4.y)), t4.x = a2, t4.y = o2, t4) : (a2 = yt(this.long0 + Math.atan2(t4.x * i2, e2 * this.cos_p14 * s2 - t4.y * this.sin_p14 * i2)), t4.x = a2, t4.y = o2, t4));
  }
  var Fs = { init: ks, forward: qs, inverse: Bs, names: ["ortho"] }, Ls = { FRONT: 1, RIGHT: 2, BACK: 3, LEFT: 4, TOP: 5, BOTTOM: 6 }, js = { AREA_0: 1, AREA_1: 2, AREA_2: 3, AREA_3: 4 };
  function Ds() {
    this.x0 = this.x0 || 0, this.y0 = this.y0 || 0, this.lat0 = this.lat0 || 0, this.long0 = this.long0 || 0, this.lat_ts = this.lat_ts || 0, this.title = this.title || "Quadrilateralized Spherical Cube", this.lat0 >= c - m / 2 ? this.face = Ls.TOP : this.lat0 <= -(c - m / 2) ? this.face = Ls.BOTTOM : Math.abs(this.long0) <= m ? this.face = Ls.FRONT : Math.abs(this.long0) <= c + m ? this.face = this.long0 > 0 ? Ls.RIGHT : Ls.LEFT : this.face = Ls.BACK, 0 !== this.es && (this.one_minus_f = 1 - (this.a - this.b) / this.a, this.one_minus_f_squared = this.one_minus_f * this.one_minus_f);
  }
  function Us(t4) {
    var e2, r2, i2, s2, n2, a2, o2 = { x: 0, y: 0 }, h2 = { value: 0 };
    if (t4.x -= this.long0, e2 = 0 !== this.es ? Math.atan(this.one_minus_f_squared * Math.tan(t4.y)) : t4.y, r2 = t4.x, this.face === Ls.TOP)
      s2 = c - e2, r2 >= m && r2 <= c + m ? (h2.value = js.AREA_0, i2 = r2 - c) : r2 > c + m || r2 <= -(c + m) ? (h2.value = js.AREA_1, i2 = r2 > 0 ? r2 - x : r2 + x) : r2 > -(c + m) && r2 <= -m ? (h2.value = js.AREA_2, i2 = r2 + c) : (h2.value = js.AREA_3, i2 = r2);
    else if (this.face === Ls.BOTTOM)
      s2 = c + e2, r2 >= m && r2 <= c + m ? (h2.value = js.AREA_0, i2 = -r2 + c) : r2 < m && r2 >= -m ? (h2.value = js.AREA_1, i2 = -r2) : r2 < -m && r2 >= -(c + m) ? (h2.value = js.AREA_2, i2 = -r2 - c) : (h2.value = js.AREA_3, i2 = r2 > 0 ? -r2 + x : -r2 - x);
    else {
      var l2, u2, d2, p2, f2, _2;
      this.face === Ls.RIGHT ? r2 = Xs(r2, +c) : this.face === Ls.BACK ? r2 = Xs(r2, 3.14159265359) : this.face === Ls.LEFT && (r2 = Xs(r2, -c)), p2 = Math.sin(e2), f2 = Math.cos(e2), _2 = Math.sin(r2), l2 = f2 * Math.cos(r2), u2 = f2 * _2, d2 = p2, this.face === Ls.FRONT ? i2 = Hs(s2 = Math.acos(l2), d2, u2, h2) : this.face === Ls.RIGHT ? i2 = Hs(s2 = Math.acos(u2), d2, -l2, h2) : this.face === Ls.BACK ? i2 = Hs(s2 = Math.acos(-l2), d2, -u2, h2) : this.face === Ls.LEFT ? i2 = Hs(s2 = Math.acos(-u2), d2, l2, h2) : (s2 = i2 = 0, h2.value = js.AREA_0);
    }
    return a2 = Math.atan(12 / x * (i2 + Math.acos(Math.sin(i2) * Math.cos(m)) - c)), n2 = Math.sqrt((1 - Math.cos(s2)) / (Math.cos(a2) * Math.cos(a2)) / (1 - Math.cos(Math.atan(1 / Math.cos(i2))))), h2.value === js.AREA_1 ? a2 += c : h2.value === js.AREA_2 ? a2 += x : h2.value === js.AREA_3 && (a2 += 1.5 * x), o2.x = n2 * Math.cos(a2), o2.y = n2 * Math.sin(a2), o2.x = o2.x * this.a + this.x0, o2.y = o2.y * this.a + this.y0, t4.x = o2.x, t4.y = o2.y, t4;
  }
  function Ws(t4) {
    var e2, r2, i2, s2, n2, a2, o2, h2, l2, u2, d2, p2, f2 = { lam: 0, phi: 0 }, _2 = { value: 0 };
    if (t4.x = (t4.x - this.x0) / this.a, t4.y = (t4.y - this.y0) / this.a, r2 = Math.atan(Math.sqrt(t4.x * t4.x + t4.y * t4.y)), e2 = Math.atan2(t4.y, t4.x), t4.x >= 0 && t4.x >= Math.abs(t4.y) ? _2.value = js.AREA_0 : t4.y >= 0 && t4.y >= Math.abs(t4.x) ? (_2.value = js.AREA_1, e2 -= c) : t4.x < 0 && -t4.x >= Math.abs(t4.y) ? (_2.value = js.AREA_2, e2 = e2 < 0 ? e2 + x : e2 - x) : (_2.value = js.AREA_3, e2 += c), l2 = x / 12 * Math.tan(e2), n2 = Math.sin(l2) / (Math.cos(l2) - 1 / Math.sqrt(2)), a2 = Math.atan(n2), (o2 = 1 - (i2 = Math.cos(e2)) * i2 * (s2 = Math.tan(r2)) * s2 * (1 - Math.cos(Math.atan(1 / Math.cos(a2))))) < -1 ? o2 = -1 : o2 > 1 && (o2 = 1), this.face === Ls.TOP)
      h2 = Math.acos(o2), f2.phi = c - h2, _2.value === js.AREA_0 ? f2.lam = a2 + c : _2.value === js.AREA_1 ? f2.lam = a2 < 0 ? a2 + x : a2 - x : _2.value === js.AREA_2 ? f2.lam = a2 - c : f2.lam = a2;
    else if (this.face === Ls.BOTTOM)
      h2 = Math.acos(o2), f2.phi = h2 - c, _2.value === js.AREA_0 ? f2.lam = -a2 + c : _2.value === js.AREA_1 ? f2.lam = -a2 : _2.value === js.AREA_2 ? f2.lam = -a2 - c : f2.lam = a2 < 0 ? -a2 - x : -a2 + x;
    else {
      var y2, m2, g2;
      l2 = (y2 = o2) * y2, m2 = (l2 += (g2 = l2 >= 1 ? 0 : Math.sqrt(1 - l2) * Math.sin(a2)) * g2) >= 1 ? 0 : Math.sqrt(1 - l2), _2.value === js.AREA_1 ? (l2 = m2, m2 = -g2, g2 = l2) : _2.value === js.AREA_2 ? (m2 = -m2, g2 = -g2) : _2.value === js.AREA_3 && (l2 = m2, m2 = g2, g2 = -l2), this.face === Ls.RIGHT ? (l2 = y2, y2 = -m2, m2 = l2) : this.face === Ls.BACK ? (y2 = -y2, m2 = -m2) : this.face === Ls.LEFT && (l2 = y2, y2 = m2, m2 = -l2), f2.phi = Math.acos(-g2) - c, f2.lam = Math.atan2(m2, y2), this.face === Ls.RIGHT ? f2.lam = Xs(f2.lam, -c) : this.face === Ls.BACK ? f2.lam = Xs(f2.lam, -3.14159265359) : this.face === Ls.LEFT && (f2.lam = Xs(f2.lam, +c));
    }
    return 0 !== this.es && (u2 = f2.phi < 0 ? 1 : 0, d2 = Math.tan(f2.phi), p2 = this.b / Math.sqrt(d2 * d2 + this.one_minus_f_squared), f2.phi = Math.atan(Math.sqrt(this.a * this.a - p2 * p2) / (this.one_minus_f * p2)), u2 && (f2.phi = -f2.phi)), f2.lam += this.long0, t4.x = f2.lam, t4.y = f2.phi, t4;
  }
  function Hs(t4, e2, r2, i2) {
    var s2;
    return t4 < f ? (i2.value = js.AREA_0, s2 = 0) : (s2 = Math.atan2(e2, r2), Math.abs(s2) <= m ? i2.value = js.AREA_0 : s2 > m && s2 <= c + m ? (i2.value = js.AREA_1, s2 -= c) : s2 > c + m || s2 <= -(c + m) ? (i2.value = js.AREA_2, s2 = s2 >= 0 ? s2 - x : s2 + x) : (i2.value = js.AREA_3, s2 += c)), s2;
  }
  function Xs(t4, e2) {
    var r2 = t4 + e2;
    return r2 < -3.14159265359 ? r2 += g : r2 > 3.14159265359 && (r2 -= g), r2;
  }
  var Qs = { init: Ds, forward: Us, inverse: Ws, names: ["Quadrilateralized Spherical Cube", "Quadrilateralized_Spherical_Cube", "qsc"] }, Zs = [[1, 22199e-21, -715515e-10, 31103e-10], [0.9986, -482243e-9, -24897e-9, -13309e-10], [0.9954, -83103e-8, -448605e-10, -986701e-12], [0.99, -135364e-8, -59661e-9, 36777e-10], [0.9822, -167442e-8, -449547e-11, -572411e-11], [0.973, -214868e-8, -903571e-10, 18736e-12], [0.96, -305085e-8, -900761e-10, 164917e-11], [0.9427, -382792e-8, -653386e-10, -26154e-10], [0.9216, -467746e-8, -10457e-8, 481243e-11], [0.8962, -536223e-8, -323831e-10, -543432e-11], [0.8679, -609363e-8, -113898e-9, 332484e-11], [0.835, -698325e-8, -640253e-10, 934959e-12], [0.7986, -755338e-8, -500009e-10, 935324e-12], [0.7597, -798324e-8, -35971e-9, -227626e-11], [0.7186, -851367e-8, -701149e-10, -86303e-10], [0.6732, -986209e-8, -199569e-9, 191974e-10], [0.6213, -0.010418, 883923e-10, 624051e-11], [0.5722, -906601e-8, 182e-6, 624051e-11], [0.5322, -677797e-8, 275608e-9, 624051e-11]], Ys = [[-520417e-23, 0.0124, 121431e-23, -845284e-16], [0.062, 0.0124, -126793e-14, 422642e-15], [0.124, 0.0124, 507171e-14, -160604e-14], [0.186, 0.0123999, -190189e-13, 600152e-14], [0.248, 0.0124002, 710039e-13, -224e-10], [0.31, 0.0123992, -264997e-12, 835986e-13], [0.372, 0.0124029, 988983e-12, -311994e-12], [0.434, 0.0123893, -369093e-11, -435621e-12], [0.4958, 0.0123198, -102252e-10, -345523e-12], [0.5571, 0.0121916, -154081e-10, -582288e-12], [0.6176, 0.0119938, -241424e-10, -525327e-12], [0.6769, 0.011713, -320223e-10, -516405e-12], [0.7346, 0.0113541, -397684e-10, -609052e-12], [0.7903, 0.0109107, -489042e-10, -104739e-11], [0.8435, 0.0103431, -64615e-9, -140374e-14], [0.8936, 969686e-8, -64636e-9, -8547e-9], [0.9394, 840947e-8, -192841e-9, -42106e-10], [0.9761, 616527e-8, -256e-6, -42106e-10], [1, 328947e-8, -319159e-9, -42106e-10]], Js = 0.8487, Ks = 1.3523, tn = y / 5, en = 1 / tn, rn = 18, sn = function(t4, e2) {
    return t4[0] + e2 * (t4[1] + e2 * (t4[2] + e2 * t4[3]));
  }, nn = function(t4, e2) {
    return t4[1] + e2 * (2 * t4[2] + 3 * e2 * t4[3]);
  };
  function an(t4, e2, r2, i2) {
    for (var s2 = e2; i2; --i2) {
      var n2 = t4(s2);
      if (s2 -= n2, Math.abs(n2) < r2)
        break;
    }
    return s2;
  }
  function on() {
    this.x0 = this.x0 || 0, this.y0 = this.y0 || 0, this.long0 = this.long0 || 0, this.es = 0, this.title = this.title || "Robinson";
  }
  function hn(t4) {
    var e2 = yt(t4.x - this.long0), r2 = Math.abs(t4.y), i2 = Math.floor(r2 * tn);
    i2 < 0 ? i2 = 0 : i2 >= rn && (i2 = rn - 1), r2 = y * (r2 - en * i2);
    var s2 = { x: sn(Zs[i2], r2) * e2, y: sn(Ys[i2], r2) };
    return t4.y < 0 && (s2.y = -s2.y), s2.x = s2.x * this.a * Js + this.x0, s2.y = s2.y * this.a * Ks + this.y0, s2;
  }
  function ln(t4) {
    var e2 = { x: (t4.x - this.x0) / (this.a * Js), y: Math.abs(t4.y - this.y0) / (this.a * Ks) };
    if (e2.y >= 1)
      e2.x /= Zs[rn][0], e2.y = t4.y < 0 ? -c : c;
    else {
      var r2 = Math.floor(e2.y * rn);
      for (r2 < 0 ? r2 = 0 : r2 >= rn && (r2 = rn - 1); ; )
        if (Ys[r2][0] > e2.y)
          --r2;
        else {
          if (!(Ys[r2 + 1][0] <= e2.y))
            break;
          ++r2;
        }
      var i2 = Ys[r2], s2 = 5 * (e2.y - i2[0]) / (Ys[r2 + 1][0] - i2[0]);
      s2 = an(function(t5) {
        return (sn(i2, t5) - e2.y) / nn(i2, t5);
      }, s2, f, 100), e2.x /= sn(Zs[r2], s2), e2.y = (5 * r2 + s2) * _, t4.y < 0 && (e2.y = -e2.y);
    }
    return e2.x = yt(e2.x + this.long0), e2;
  }
  var cn = { init: on, forward: hn, inverse: ln, names: ["Robinson", "robin"] };
  function un() {
    this.name = "geocent";
  }
  function dn(t4) {
    return ie(t4, this.es, this.a);
  }
  function pn(t4) {
    return se(t4, this.es, this.a, this.b);
  }
  var fn = { init: un, forward: dn, inverse: pn, names: ["Geocentric", "geocentric", "geocent", "Geocent"] }, _n = { N_POLE: 0, S_POLE: 1, EQUIT: 2, OBLIQ: 3 }, yn = { h: { def: 1e5, num: true }, azi: { def: 0, num: true, degrees: true }, tilt: { def: 0, num: true, degrees: true }, long0: { def: 0, num: true }, lat0: { def: 0, num: true } };
  function mn() {
    if (Object.keys(yn).forEach(function(t5) {
      if (void 0 === this[t5])
        this[t5] = yn[t5].def;
      else {
        if (yn[t5].num && isNaN(this[t5]))
          throw new Error("Invalid parameter value, must be numeric " + t5 + " = " + this[t5]);
        yn[t5].num && (this[t5] = parseFloat(this[t5]));
      }
      yn[t5].degrees && (this[t5] = this[t5] * _);
    }.bind(this)), Math.abs(Math.abs(this.lat0) - c) < f ? this.mode = this.lat0 < 0 ? _n.S_POLE : _n.N_POLE : Math.abs(this.lat0) < f ? this.mode = _n.EQUIT : (this.mode = _n.OBLIQ, this.sinph0 = Math.sin(this.lat0), this.cosph0 = Math.cos(this.lat0)), this.pn1 = this.h / this.a, this.pn1 <= 0 || this.pn1 > 1e10)
      throw new Error("Invalid height");
    this.p = 1 + this.pn1, this.rp = 1 / this.p, this.h1 = 1 / this.pn1, this.pfact = (this.p + 1) * this.h1, this.es = 0;
    var t4 = this.tilt, e2 = this.azi;
    this.cg = Math.cos(e2), this.sg = Math.sin(e2), this.cw = Math.cos(t4), this.sw = Math.sin(t4);
  }
  function gn(t4) {
    t4.x -= this.long0;
    var e2, r2, i2, s2, n2 = Math.sin(t4.y), a2 = Math.cos(t4.y), o2 = Math.cos(t4.x);
    switch (this.mode) {
      case _n.OBLIQ:
        r2 = this.sinph0 * n2 + this.cosph0 * a2 * o2;
        break;
      case _n.EQUIT:
        r2 = a2 * o2;
        break;
      case _n.S_POLE:
        r2 = -n2;
        break;
      case _n.N_POLE:
        r2 = n2;
    }
    switch (e2 = (r2 = this.pn1 / (this.p - r2)) * a2 * Math.sin(t4.x), this.mode) {
      case _n.OBLIQ:
        r2 *= this.cosph0 * n2 - this.sinph0 * a2 * o2;
        break;
      case _n.EQUIT:
        r2 *= n2;
        break;
      case _n.N_POLE:
        r2 *= -a2 * o2;
        break;
      case _n.S_POLE:
        r2 *= a2 * o2;
    }
    return s2 = 1 / ((i2 = r2 * this.cg + e2 * this.sg) * this.sw * this.h1 + this.cw), e2 = (e2 * this.cg - r2 * this.sg) * this.cw * s2, r2 = i2 * s2, t4.x = e2 * this.a, t4.y = r2 * this.a, t4;
  }
  function xn(t4) {
    t4.x /= this.a, t4.y /= this.a;
    var e2, r2, i2, s2 = { x: t4.x, y: t4.y };
    i2 = 1 / (this.pn1 - t4.y * this.sw), e2 = this.pn1 * t4.x * i2, r2 = this.pn1 * t4.y * this.cw * i2, t4.x = e2 * this.cg + r2 * this.sg, t4.y = r2 * this.cg - e2 * this.sg;
    var n2 = _r(t4.x, t4.y);
    if (Math.abs(n2) < f)
      s2.x = 0, s2.y = t4.y;
    else {
      var a2, o2;
      switch (o2 = 1 - n2 * n2 * this.pfact, o2 = (this.p - Math.sqrt(o2)) / (this.pn1 / n2 + n2 / this.pn1), a2 = Math.sqrt(1 - o2 * o2), this.mode) {
        case _n.OBLIQ:
          s2.y = Math.asin(a2 * this.sinph0 + t4.y * o2 * this.cosph0 / n2), t4.y = (a2 - this.sinph0 * Math.sin(s2.y)) * n2, t4.x *= o2 * this.cosph0;
          break;
        case _n.EQUIT:
          s2.y = Math.asin(t4.y * o2 / n2), t4.y = a2 * n2, t4.x *= o2;
          break;
        case _n.N_POLE:
          s2.y = Math.asin(a2), t4.y = -t4.y;
          break;
        case _n.S_POLE:
          s2.y = -Math.asin(a2);
      }
      s2.x = Math.atan2(t4.x, t4.y);
    }
    return t4.x = s2.x + this.long0, t4.y = s2.y, t4;
  }
  var Mn = { init: mn, forward: gn, inverse: xn, names: ["Tilted_Perspective", "tpers"] };
  function wn() {
    if (this.flip_axis = "x" === this.sweep ? 1 : 0, this.h = Number(this.h), this.radius_g_1 = this.h / this.a, this.radius_g_1 <= 0 || this.radius_g_1 > 1e10)
      throw new Error();
    if (this.radius_g = 1 + this.radius_g_1, this.C = this.radius_g * this.radius_g - 1, 0 !== this.es) {
      var t4 = 1 - this.es, e2 = 1 / t4;
      this.radius_p = Math.sqrt(t4), this.radius_p2 = t4, this.radius_p_inv2 = e2, this.shape = "ellipse";
    } else
      this.radius_p = 1, this.radius_p2 = 1, this.radius_p_inv2 = 1, this.shape = "sphere";
    this.title || (this.title = "Geostationary Satellite View");
  }
  function bn(t4) {
    var e2, r2, i2, s2, n2 = t4.x, a2 = t4.y;
    if (n2 -= this.long0, "ellipse" === this.shape) {
      a2 = Math.atan(this.radius_p2 * Math.tan(a2));
      var o2 = this.radius_p / _r(this.radius_p * Math.cos(a2), Math.sin(a2));
      if (r2 = o2 * Math.cos(n2) * Math.cos(a2), i2 = o2 * Math.sin(n2) * Math.cos(a2), s2 = o2 * Math.sin(a2), (this.radius_g - r2) * r2 - i2 * i2 - s2 * s2 * this.radius_p_inv2 < 0)
        return t4.x = Number.NaN, t4.y = Number.NaN, t4;
      e2 = this.radius_g - r2, this.flip_axis ? (t4.x = this.radius_g_1 * Math.atan(i2 / _r(s2, e2)), t4.y = this.radius_g_1 * Math.atan(s2 / e2)) : (t4.x = this.radius_g_1 * Math.atan(i2 / e2), t4.y = this.radius_g_1 * Math.atan(s2 / _r(i2, e2)));
    } else
      "sphere" === this.shape && (e2 = Math.cos(a2), r2 = Math.cos(n2) * e2, i2 = Math.sin(n2) * e2, s2 = Math.sin(a2), e2 = this.radius_g - r2, this.flip_axis ? (t4.x = this.radius_g_1 * Math.atan(i2 / _r(s2, e2)), t4.y = this.radius_g_1 * Math.atan(s2 / e2)) : (t4.x = this.radius_g_1 * Math.atan(i2 / e2), t4.y = this.radius_g_1 * Math.atan(s2 / _r(i2, e2))));
    return t4.x = t4.x * this.a, t4.y = t4.y * this.a, t4;
  }
  function vn(t4) {
    var e2, r2, i2, s2, n2 = -1, a2 = 0, o2 = 0;
    if (t4.x = t4.x / this.a, t4.y = t4.y / this.a, "ellipse" === this.shape) {
      this.flip_axis ? (o2 = Math.tan(t4.y / this.radius_g_1), a2 = Math.tan(t4.x / this.radius_g_1) * _r(1, o2)) : (a2 = Math.tan(t4.x / this.radius_g_1), o2 = Math.tan(t4.y / this.radius_g_1) * _r(1, a2));
      var h2 = o2 / this.radius_p;
      if (e2 = a2 * a2 + h2 * h2 + n2 * n2, (i2 = (r2 = 2 * this.radius_g * n2) * r2 - 4 * e2 * this.C) < 0)
        return t4.x = Number.NaN, t4.y = Number.NaN, t4;
      s2 = (-r2 - Math.sqrt(i2)) / (2 * e2), n2 = this.radius_g + s2 * n2, a2 *= s2, o2 *= s2, t4.x = Math.atan2(a2, n2), t4.y = Math.atan(o2 * Math.cos(t4.x) / n2), t4.y = Math.atan(this.radius_p_inv2 * Math.tan(t4.y));
    } else if ("sphere" === this.shape) {
      if (this.flip_axis ? (o2 = Math.tan(t4.y / this.radius_g_1), a2 = Math.tan(t4.x / this.radius_g_1) * Math.sqrt(1 + o2 * o2)) : (a2 = Math.tan(t4.x / this.radius_g_1), o2 = Math.tan(t4.y / this.radius_g_1) * Math.sqrt(1 + a2 * a2)), e2 = a2 * a2 + o2 * o2 + n2 * n2, (i2 = (r2 = 2 * this.radius_g * n2) * r2 - 4 * e2 * this.C) < 0)
        return t4.x = Number.NaN, t4.y = Number.NaN, t4;
      s2 = (-r2 - Math.sqrt(i2)) / (2 * e2), n2 = this.radius_g + s2 * n2, a2 *= s2, o2 *= s2, t4.x = Math.atan2(a2, n2), t4.y = Math.atan(o2 * Math.cos(t4.x) / n2);
    }
    return t4.x = t4.x + this.long0, t4;
  }
  var Sn = { init: wn, forward: bn, inverse: vn, names: ["Geostationary Satellite View", "Geostationary_Satellite", "geos"] }, Cn = 1.340264, En = -0.081106, Pn = 893e-6, Tn = 3796e-6, In2 = Math.sqrt(3) / 2;
  function zn() {
    this.es = 0, this.long0 = void 0 !== this.long0 ? this.long0 : 0;
  }
  function An(t4) {
    var e2 = yt(t4.x - this.long0), r2 = t4.y, i2 = Math.asin(In2 * Math.sin(r2)), s2 = i2 * i2, n2 = s2 * s2 * s2;
    return t4.x = e2 * Math.cos(i2) / (In2 * (Cn + 3 * En * s2 + n2 * (7 * Pn + 9 * Tn * s2))), t4.y = i2 * (Cn + En * s2 + n2 * (Pn + Tn * s2)), t4.x = this.a * t4.x + this.x0, t4.y = this.a * t4.y + this.y0, t4;
  }
  function On(t4) {
    t4.x = (t4.x - this.x0) / this.a, t4.y = (t4.y - this.y0) / this.a;
    var e2, r2, i2, s2, n2 = 1e-9, a2 = 12, o2 = t4.y;
    for (s2 = 0; s2 < a2 && (o2 -= i2 = (o2 * (Cn + En * (e2 = o2 * o2) + (r2 = e2 * e2 * e2) * (Pn + Tn * e2)) - t4.y) / (Cn + 3 * En * e2 + r2 * (7 * Pn + 9 * Tn * e2)), !(Math.abs(i2) < n2)); ++s2)
      ;
    return r2 = (e2 = o2 * o2) * e2 * e2, t4.x = In2 * t4.x * (Cn + 3 * En * e2 + r2 * (7 * Pn + 9 * Tn * e2)) / Math.cos(o2), t4.y = Math.asin(Math.sin(o2) / In2), t4.x = yt(t4.x + this.long0), t4;
  }
  var Nn = { init: zn, forward: An, inverse: On, names: ["eqearth", "Equal Earth", "Equal_Earth"] }, Rn = 1e-10;
  function $n() {
    var t4;
    if (this.phi1 = this.lat1, Math.abs(this.phi1) < Rn)
      throw new Error();
    this.es ? (this.en = ar(this.es), this.m1 = or(this.phi1, this.am1 = Math.sin(this.phi1), t4 = Math.cos(this.phi1), this.en), this.am1 = t4 / (Math.sqrt(1 - this.es * this.am1 * this.am1) * this.am1), this.inverse = Gn, this.forward = Vn) : (Math.abs(this.phi1) + Rn >= c ? this.cphi1 = 0 : this.cphi1 = 1 / Math.tan(this.phi1), this.inverse = qn, this.forward = kn);
  }
  function Vn(t4) {
    var e2, r2, i2, s2 = yt(t4.x - (this.long0 || 0)), n2 = t4.y;
    return e2 = this.am1 + this.m1 - or(n2, r2 = Math.sin(n2), i2 = Math.cos(n2), this.en), r2 = i2 * s2 / (e2 * Math.sqrt(1 - this.es * r2 * r2)), t4.x = e2 * Math.sin(r2), t4.y = this.am1 - e2 * Math.cos(r2), t4.x = this.a * t4.x + (this.x0 || 0), t4.y = this.a * t4.y + (this.y0 || 0), t4;
  }
  function Gn(t4) {
    var e2, r2, i2, s2;
    if (t4.x = (t4.x - (this.x0 || 0)) / this.a, t4.y = (t4.y - (this.y0 || 0)) / this.a, r2 = _r(t4.x, t4.y = this.am1 - t4.y), s2 = lr(this.am1 + this.m1 - r2, this.es, this.en), (e2 = Math.abs(s2)) < c)
      e2 = Math.sin(s2), i2 = r2 * Math.atan2(t4.x, t4.y) * Math.sqrt(1 - this.es * e2 * e2) / Math.cos(s2);
    else {
      if (!(Math.abs(e2 - c) <= Rn))
        throw new Error();
      i2 = 0;
    }
    return t4.x = yt(i2 + (this.long0 || 0)), t4.y = fi(s2), t4;
  }
  function kn(t4) {
    var e2, r2, i2 = yt(t4.x - (this.long0 || 0)), s2 = t4.y;
    return r2 = this.cphi1 + this.phi1 - s2, Math.abs(r2) > Rn ? (t4.x = r2 * Math.sin(e2 = i2 * Math.cos(s2) / r2), t4.y = this.cphi1 - r2 * Math.cos(e2)) : t4.x = t4.y = 0, t4.x = this.a * t4.x + (this.x0 || 0), t4.y = this.a * t4.y + (this.y0 || 0), t4;
  }
  function qn(t4) {
    var e2, r2;
    t4.x = (t4.x - (this.x0 || 0)) / this.a, t4.y = (t4.y - (this.y0 || 0)) / this.a;
    var i2 = _r(t4.x, t4.y = this.cphi1 - t4.y);
    if (r2 = this.cphi1 + this.phi1 - i2, Math.abs(r2) > c)
      throw new Error();
    return e2 = Math.abs(Math.abs(r2) - c) <= Rn ? 0 : i2 * Math.atan2(t4.x, t4.y) / Math.cos(r2), t4.x = yt(e2 + (this.long0 || 0)), t4.y = fi(r2), t4;
  }
  var Bn = { init: $n, names: ["bonne", "Bonne (Werner lat_1=90)"] };
  function Fn(t4) {
    t4.Proj.projections.add(pr), t4.Proj.projections.add(Cr), t4.Proj.projections.add(Tr), t4.Proj.projections.add(kr), t4.Proj.projections.add(jr), t4.Proj.projections.add(Hr), t4.Proj.projections.add(Kr), t4.Proj.projections.add(ii), t4.Proj.projections.add(oi), t4.Proj.projections.add(xi), t4.Proj.projections.add(Vi), t4.Proj.projections.add(Li), t4.Proj.projections.add(Wi), t4.Proj.projections.add(Yi), t4.Proj.projections.add(es), t4.Proj.projections.add(as), t4.Proj.projections.add(cs), t4.Proj.projections.add(fs), t4.Proj.projections.add(xs), t4.Proj.projections.add(vs), t4.Proj.projections.add(Ps), t4.Proj.projections.add(As), t4.Proj.projections.add(Gs), t4.Proj.projections.add(Fs), t4.Proj.projections.add(Qs), t4.Proj.projections.add(cn), t4.Proj.projections.add(fn), t4.Proj.projections.add(Mn), t4.Proj.projections.add(Sn), t4.Proj.projections.add(Nn), t4.Proj.projections.add(Bn);
  }
  const Ln = Object.assign(we, { defaultDatum: "WGS84", Proj: ee, WGS84: new ee("WGS84"), Point: He, toPoint: pe, defs: st, nadgrid: Ft, transform: me, mgrs: ze, version: "2.19.7" });
  return Fn(Ln), Ln;
}();
var proj4 = proj4Src.exports;
let globalId = 1;
const _inputArray = new Array(2);
class GenericDefinedProjection extends Projection {
  constructor(t, e, r) {
    super(), t || (t = "custom_" + globalId++), this._name = t, this._parameters = e, proj4.defs(this._name) || proj4.defs(this._name, e), r && r.isBox3 && (this._geoBoundingBox = r);
  }
  projectCoordinate(t, e, r) {
    _inputArray[0] = t.x, _inputArray[1] = t.y;
    const i = this.geoBoundingBox;
    _inputArray[0] < i.min.x && (_inputArray[0] = i.min.x), _inputArray[0] > i.max.x && (_inputArray[0] = i.max.x), _inputArray[1] < i.min.y && (_inputArray[1] = i.min.y), _inputArray[1] > i.max.y && (_inputArray[1] = i.max.y);
    const s = proj4("EPSG:4326", this.name, _inputArray);
    if (e || (e = new Vector3$1()), r) {
      const r2 = this.projectedBoundingBox;
      e.x = extendProjectCoordinate(t.x, s[0], i.max.x, r2.max.x), e.y = extendProjectCoordinate(t.y, s[1], i.max.y, r2.max.y);
    } else
      e.x = s[0], e.y = s[1];
    return e.z = t.z, e;
  }
  unprojectCoordinate(t, e, r) {
    const i = this.projectedBoundingBox;
    _inputArray[0] = t.x, _inputArray[1] = t.y, _inputArray[0] < i.min.x && (_inputArray[0] = i.min.x), _inputArray[0] > i.max.x && (_inputArray[0] = i.max.x), _inputArray[1] < i.min.y && (_inputArray[1] = i.min.y), _inputArray[1] > i.max.y && (_inputArray[1] = i.max.y);
    const s = proj4(this.name, "EPSG:4326", _inputArray);
    if (e || (e = new Vector3$1()), r) {
      const r2 = this.geoBoundingBox;
      e.x = extendUnprojectCoordinate(t.x, s[0], r2.max.x, i.max.x), e.y = extendUnprojectCoordinate(t.y, s[1], r2.max.y, i.max.y);
    } else
      e.x = s[0], e.y = s[1];
    return e.z = t.z, e;
  }
  get name() {
    return this._name;
  }
}
const _cache = {}, projectionDefs = { "EPSG:5070": { parameters: "+proj=aea +lat_0=23 +lon_0=-96 +lat_1=29.5 +lat_2=45.5 +x_0=0 +y_0=0 +ellps=GRS80 +towgs84=0,0,0,0,0,0,0 +units=m +no_defs +type=crs", projectBoundingBoxMethod: projectBoundingBoxMethods.FOUR_CORNERS_WITH_EQUATOR, geoBoundingBox: [[-172.54, 23.81, 0], [-47.74, 86.46, 0]] }, "EPSG:8857": { parameters: "+proj=eqearth +lon_0=0 +x_0=0 +y_0=0 +datum=WGS84 +units=m +no_defs +type=crs", projectBoundingBoxMethod: projectBoundingBoxMethods.FOUR_CORNERS_WITH_EQUATOR } };
for (let t = 1; t <= 60; t++) {
  let e = 6 * (t - 1) - 180, r = 6 * t - 180;
  e -= 12, r += 12, e < -180 && (e = -180), r > 180 && (r = 180), projectionDefs[`EPSG:${32600 + t}`] = { projectBoundingBoxMethod: projectBoundingBoxMethods.FOUR_CORNERS_WITH_EQUATOR, geoBoundingBox: [[e, -80, 0], [r, 84, 0]] }, projectionDefs[`EPSG:${32700 + t}`] = { projectBoundingBoxMethod: projectBoundingBoxMethods.FOUR_CORNERS_WITH_EQUATOR, geoBoundingBox: [[e, -80, 0], [r, 84, 0]] };
}
for (let t = 0; t < 11; t++) {
  let e = t + 13, r = 6 * e - 3, i = 1e6 * e + 5e5;
  projectionDefs[`EPSG:${4491 + t}`] = { parameters: `+proj=tmerc +lat_0=0 +lon_0=${r} +k=1 +x_0=${i} +y_0=0 +ellps=GRS80 +units=m +no_defs +type=crs`, projectBoundingBoxMethod: projectBoundingBoxMethods.FOUR_CORNERS_WITH_EQUATOR, geoBoundingBox: [[r - 3, 3, 0], [r + 3, 54, 0]] }, projectionDefs[`EPSG:${4502 + t}`] = { parameters: `+proj=tmerc +lat_0=0 +lon_0=${r} +k=1 +x_0=500000 +y_0=0 +ellps=GRS80 +units=m +no_defs +type=crs`, projectBoundingBoxMethod: projectBoundingBoxMethods.FOUR_CORNERS_WITH_EQUATOR, geoBoundingBox: [[r - 3, 3, 0], [r + 3, 54, 0]] }, e = 25 + 2 * t, r = 3 * e, i = 1e6 * e + 5e5, projectionDefs["EPSG:" + (4513 + 2 * t)] = { parameters: `+proj=tmerc +lat_0=0 +lon_0=${r} +k=1 +x_0=${i} +y_0=0 +ellps=GRS80 +units=m +no_defs +type=crs`, projectBoundingBoxMethod: projectBoundingBoxMethods.FOUR_CORNERS_WITH_EQUATOR, geoBoundingBox: [[r - 1.5, 3, 0], [r + 1.5, 54, 0]] }, projectionDefs["EPSG:" + (4534 + 2 * t)] = { parameters: `+proj=tmerc +lat_0=0 +lon_0=${r} +k=1 +x_0=500000 +y_0=0 +ellps=GRS80 +units=m +no_defs +type=crs`, projectBoundingBoxMethod: projectBoundingBoxMethods.FOUR_CORNERS_WITH_EQUATOR, geoBoundingBox: [[r - 1.5, 3, 0], [r + 1.5, 54, 0]] }, e = 25 + 2 * t + 1, t < 10 && (r = 3 * e, i = 1e6 * e + 5e5, projectionDefs["EPSG:" + (4513 + 2 * t + 1)] = { parameters: `+proj=tmerc +lat_0=0 +lon_0=${r} +k=1 +x_0=${i} +y_0=0 +ellps=GRS80 +units=m +no_defs +type=crs`, projectBoundingBoxMethod: projectBoundingBoxMethods.FOUR_CORNERS_WITH_EQUATOR, geoBoundingBox: [[r - 1.5, 3, 0], [r + 1.5, 54, 0]] }, projectionDefs["EPSG:" + (4534 + 2 * t + 1)] = { parameters: `+proj=tmerc +lat_0=0 +lon_0=${r} +k=1 +x_0=500000 +y_0=0 +ellps=GRS80 +units=m +no_defs +type=crs`, projectBoundingBoxMethod: projectBoundingBoxMethods.FOUR_CORNERS_WITH_EQUATOR, geoBoundingBox: [[r - 1.5, 3, 0], [r + 1.5, 54, 0]] });
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
          const r = projectionDefs[t];
          let i = null;
          if (r.geoBoundingBox) {
            const t4 = r.geoBoundingBox;
            i = new Box3(new Vector3$1(t4[0][0], t4[0][1], t4[0][2] || 0), new Vector3$1(t4[1][0], t4[1][1], t4[1][2] || 0));
          }
          e = new GenericDefinedProjection(t, r.parameters, i), r.projectBoundingBoxMethod && (e.projectBoundingBoxMethod = r.projectBoundingBoxMethod);
        }
        if (!e)
          throw new Error(`Unsupported projection: ${t}`);
        _cache[t] = e;
    }
  return _cache[t];
};
self.abortControllers = /* @__PURE__ */ new Map(), self.addEventListener("message", (t) => {
  const e = t.data, { type: r, tileKeys: i } = e;
  "requestTile" === r ? self.handleRequestTile(e) : "cancelTiles" === r ? self.handleCancelTileRequest(i) : "changeStyle" === r ? self.handleChangeStyle(e) : console.log(e);
}), self.handleCancelTileRequest = (t) => {
  for (let e = 0; e < t.length; e++) {
    const r = t[e], i = self.abortControllers.get(r);
    i && (i.abort(), self.abortControllers.delete(r));
  }
}, self.handleRequestTile = async (t) => {
  const { tileKey: e, fetchOptions: r = {}, workerOptions: i, url: s, z: n, x: a, reverseY: o, sourceProjectionName: h, targetProjectionName: l } = t;
  t.sourceProjection = getProjection(h), t.targetProjection = getProjection(l);
  let c = {}, u = [], d = 0;
  const p = new AbortController();
  self.abortControllers.set(e, p), r.signal = p.signal;
  let f = false;
  try {
    let h2 = await reFetch(s, { responseType: "arraybuffer", ...r }).then((t4) => t4.arrayBuffer());
    if (!self.abortControllers.has(e))
      return;
    self.abortControllers.delete(e);
    const l2 = new VectorTile(new pbf(h2)), { styleJSON: p2, defaultStyleJSON: f2, spriteData: _ } = self, { displayOptions: y } = i, m = new MapboxStyle(p2, f2, _), g = new SourceLayer(l2.layers, 0, { x: a, y: o, z: n }), x = new Executer(g, m, y, t);
    x.execute();
    const M = { background: [], fill_opaque: [], fill_water: [], fill_wood: [], fill_translucent: [], fill_extrusion_opaque: [], fill_extrusion_translucent: [], line_opaque: [], line_translucent: [], line_dashed: [], symbol: [] };
    d = x.getExecutedData(M), c = M, c.background && u.push(c.background.vertices.buffer, c.background.layerIndices.buffer, c.background.colors.buffer, c.background.normals.buffer, c.background.indices.buffer), c.fill_opaque && u.push(c.fill_opaque.vertices.buffer, c.fill_opaque.layerIndices.buffer, c.fill_opaque.colors.buffer, c.fill_opaque.normals.buffer, c.fill_opaque.uvs.buffer, c.fill_opaque.indices.buffer), c.fill_pattern && u.push(c.fill_pattern.vertices.buffer, c.fill_pattern.layerIndices.buffer, c.fill_pattern.colors.buffer, c.fill_pattern.normals.buffer, c.fill_pattern.uvs.buffer, c.fill_pattern.indices.buffer, c.fill_pattern.icons.buffer), c.fill_water && u.push(c.fill_water.vertices.buffer, c.fill_water.layerIndices.buffer, c.fill_water.colors.buffer, c.fill_water.normals.buffer, c.fill_water.uvs.buffer, c.fill_water.indices.buffer), c.fill_wood && u.push(c.fill_wood.vertices.buffer, c.fill_wood.layerIndices.buffer, c.fill_wood.colors.buffer, c.fill_wood.normals.buffer, c.fill_wood.uvs.buffer, c.fill_wood.indices.buffer), c.fill_translucent && u.push(c.fill_translucent.vertices.buffer, c.fill_translucent.layerIndices.buffer, c.fill_translucent.colors.buffer, c.fill_translucent.normals.buffer, c.fill_translucent.uvs.buffer, c.fill_translucent.indices.buffer), c.fill_extrusion_opaque && u.push(c.fill_extrusion_opaque.vertices.buffer, c.fill_extrusion_opaque.layerIndices.buffer, c.fill_extrusion_opaque.colors.buffer, c.fill_extrusion_opaque.normals.buffer, c.fill_extrusion_opaque.uvs.buffer, c.fill_extrusion_opaque.indices.buffer, c.fill_extrusion_opaque.indices_side.buffer), c.fill_extrusion_translucent && u.push(c.fill_extrusion_translucent.vertices.buffer, c.fill_extrusion_translucent.layerIndices.buffer, c.fill_extrusion_translucent.colors.buffer, c.fill_extrusion_translucent.normals.buffer, c.fill_extrusion_translucent.uvs.buffer, c.fill_extrusion_translucent.indices.buffer, c.fill_extrusion_translucent.indices_side.buffer), c.line_opaque && u.push(c.line_opaque.vertices.buffer, c.line_opaque.layerIndices.buffer, c.line_opaque.colors.buffer, c.line_opaque.normals.buffer, c.line_opaque.widths.buffer, c.line_opaque.extentVertices.buffer, c.line_opaque.indices.buffer), c.line_translucent && u.push(c.line_translucent.vertices.buffer, c.line_translucent.layerIndices.buffer, c.line_translucent.colors.buffer, c.line_translucent.normals.buffer, c.line_translucent.widths.buffer, c.line_translucent.extentVertices.buffer, c.line_translucent.indices.buffer), c.line_dashed && u.push(c.line_dashed.vertices.buffer, c.line_dashed.layerIndices.buffer, c.line_dashed.colors.buffer, c.line_dashed.normals.buffer, c.line_dashed.widths.buffer, c.line_dashed.extentVertices.buffer, c.line_dashed.indices.buffer);
  } catch (t4) {
    "AbortError" === t4.name ? (f = true, self.abortControllers.delete(e)) : console.warn(t4);
  }
  self.postMessage({ type: "responseTile", tileKey: e, content: c, isCancelled: f, id: t.id }, u);
}, self.mergePrimitives = (t) => {
  const e = {};
  return t.background && t.background.indices.length > 0 && (e.background = self.mergeAttributes(t.background)), t.fill_opaque && t.fill_opaque.indices.length > 0 && (e.fill_opaque = self.mergeAttributes(t.fill_opaque, { isFill: true })), t.fill_water && t.fill_water.indices.length > 0 && (e.fill_water = self.mergeAttributes(t.fill_water, { isFill: true })), t.fill_wood && t.fill_wood.indices.length > 0 && (e.fill_wood = self.mergeAttributes(t.fill_wood, { isFill: true })), t.fill_translucent && t.fill_translucent.indices.length > 0 && (e.fill_translucent = self.mergeAttributes(t.fill_translucent, { isFill: true })), t.line_opaque && t.line_opaque.indices.length > 0 && (e.line_opaque = self.mergeAttributes(t.line_opaque, { isLine: true })), t.line_translucent && t.line_translucent.indices.length > 0 && (e.line_translucent = self.mergeAttributes(t.line_translucent, { isLine: true })), t.line_dashed && t.line_dashed.indices.length > 0 && (e.line_dashed = self.mergeAttributes(t.line_dashed, { isLine: true })), t.fill_extrusion_opaque && t.fill_extrusion_opaque.indices.length > 0 && (e.fill_extrusion_opaque = self.mergeAttributes(t.fill_extrusion_opaque, { isBuilding: true })), t.fill_extrusion_translucent && t.fill_extrusion_translucent.indices.length > 0 && (e.fill_extrusion_translucent = self.mergeAttributes(t.fill_extrusion_translucent, { isBuilding: true })), t.symbol && t.symbol.symbols.length > 0 && (e.symbol = t.symbol), e;
}, self.mergeAttributes = (t, e = {}) => {
  const { vertices: r, layerIndices: i, colors: s, normals: n, uvs: a, widths: o, extentVertices: h, indices: l, indices_side: c } = t;
  let u = 11;
  e.isFill || e.isBuilding ? u = 13 : e.isLine && (u = 14);
  const d = r.length / 3 * u, p = new Float32Array(d);
  for (let t4 = 0; t4 < d - 1; t4 += u) {
    const l2 = t4 / u, c2 = t4 / u * 2, d2 = t4 / u * 3, f2 = t4 / u * 4;
    p[t4] = r[d2], p[t4 + 1] = r[d2 + 1], p[t4 + 2] = r[d2 + 2], p[t4 + 3] = i[l2], p[t4 + 4] = s[f2], p[t4 + 5] = s[f2 + 1], p[t4 + 6] = s[f2 + 2], p[t4 + 7] = s[f2 + 3], p[t4 + 8] = n[d2], p[t4 + 9] = n[d2 + 1], p[t4 + 10] = n[d2 + 2], e.isFill && (p[t4 + 11] = a[c2], p[t4 + 12] = a[c2 + 1]), e.isBuilding && (p[t4 + 11] = a[c2], p[t4 + 12] = a[c2 + 1]), e.isLine && (p[t4 + 11] = o[l2], p[t4 + 12] = h[c2], p[t4 + 13] = h[c2 + 1]);
  }
  const f = { attributes: p, indices: l };
  return c && c.length && (f.indices_side = c), f;
}, self.handleChangeStyle = (t) => {
  self.styleJSON = t.styleJSON, self.defaultStyleJSON = t.defaultStyleJSON, self.spriteData = t.spriteData;
};
