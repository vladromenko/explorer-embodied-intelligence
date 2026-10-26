"use strict";
// ENGINEERING_GLUE: bounded immutable scientific-payload identity, no passwords/keys.
// SHA-256 padding, constants and compression: NIST FIPS PUB180-4 §§4.2.2/5/6.2.
// https://csrc.nist.gov/pubs/fips/180-4/upd1/final (August2015), no FIPS validation claim.
(function (surface) {
  const EXPLORER_SHA256_MAX_BYTES = 8 * 1024**2;
  function fallbackSHA256(bytes) {
    // Plain LAN HTTP may lack SubtleCrypto. Bounded JPEG bytes still need an actual SHA.
    const constants = [0x428a2f98,0x71374491,0xb5c0fbcf,0xe9b5dba5,0x3956c25b,0x59f111f1,0x923f82a4,0xab1c5ed5,0xd807aa98,0x12835b01,0x243185be,0x550c7dc3,0x72be5d74,0x80deb1fe,0x9bdc06a7,0xc19bf174,0xe49b69c1,0xefbe4786,0x0fc19dc6,0x240ca1cc,0x2de92c6f,0x4a7484aa,0x5cb0a9dc,0x76f988da,0x983e5152,0xa831c66d,0xb00327c8,0xbf597fc7,0xc6e00bf3,0xd5a79147,0x06ca6351,0x14292967,0x27b70a85,0x2e1b2138,0x4d2c6dfc,0x53380d13,0x650a7354,0x766a0abb,0x81c2c92e,0x92722c85,0xa2bfe8a1,0xa81a664b,0xc24b8b70,0xc76c51a3,0xd192e819,0xd6990624,0xf40e3585,0x106aa070,0x19a4c116,0x1e376c08,0x2748774c,0x34b0bcb5,0x391c0cb3,0x4ed8aa4a,0x5b9cca4f,0x682e6ff3,0x748f82ee,0x78a5636f,0x84c87814,0x8cc70208,0x90befffa,0xa4506ceb,0xbef9a3f7,0xc67178f2];
    const hash = [0x6a09e667,0xbb67ae85,0x3c6ef372,0xa54ff53a,0x510e527f,0x9b05688c,0x1f83d9ab,0x5be0cd19];
    const data = new Uint8Array(Math.ceil((bytes.length+9)/64)*64), words = new Uint32Array(64);
    data.set(bytes);data[bytes.length] = 128;
    new DataView(data.buffer).setUint32(data.length-4,bytes.length*8,false);
    const rotate = (value,bits)=>(value>>>bits)|(value<<(32-bits));
    for (let offset=0;offset<data.length;offset+=64) {
      for (let index=0;index<16;index++) words[index]=(data[offset+4*index]<<24)|(data[offset+4*index+1]<<16)|(data[offset+4*index+2]<<8)|data[offset+4*index+3];
      for (let index=16;index<64;index++) {const a=words[index-15],b=words[index-2];words[index]=words[index-16]+(rotate(a,7)^rotate(a,18)^(a>>>3))+words[index-7]+(rotate(b,17)^rotate(b,19)^(b>>>10));}
      let [a,b,c,d,e,f,g,h]=hash;
      for (let index=0;index<64;index++) {const t1=(h+(rotate(e,6)^rotate(e,11)^rotate(e,25))+((e&f)^(~e&g))+constants[index]+words[index])|0,t2=((rotate(a,2)^rotate(a,13)^rotate(a,22))+((a&b)^(a&c)^(b&c)))|0;h=g;g=f;f=e;e=(d+t1)|0;d=c;c=b;b=a;a=(t1+t2)|0;}
      for (const [index,value] of [a,b,c,d,e,f,g,h].entries()) hash[index]=(hash[index]+value)|0;
    }
    return hash.map(value=>(value>>>0).toString(16).padStart(8,"0")).join("");
  }

  async function explorerSha256Bytes(value,options={}) {
    if (!(value instanceof Uint8Array) && !(value instanceof ArrayBuffer)) throw new TypeError("SHA256 requires Uint8Array or ArrayBuffer bytes");
    if (options === null || typeof options !== "object") throw new TypeError("SHA256 options must be an object");
    const bound = options.maxBytes ?? EXPLORER_SHA256_MAX_BYTES;
    if (!Number.isSafeInteger(bound) || bound < 0 || bound > EXPLORER_SHA256_MAX_BYTES) throw new RangeError("SHA256 bound must be an integer from0 through8MiB");
    if (value.byteLength > bound) throw new RangeError("SHA256 scientific payload exceeds bounded8MiB input");
    // Copy before any await: later mutation of the caller's view cannot change identity.
    const bytes = new Uint8Array(value instanceof ArrayBuffer ? new Uint8Array(value) : value);
    const crypto = Object.hasOwn(options,"crypto") ? options.crypto : surface.crypto;
    if (crypto?.subtle) {
      const result = new Uint8Array(await crypto.subtle.digest("SHA-256",bytes));
      if (result.length !== 32) throw new Error("SHA256 provider returned an invalid digest length");
      return [...result].map(part=>part.toString(16).padStart(2,"0")).join("");
    }
    return fallbackSHA256(bytes);
  }
  const exported = {explorerSha256Bytes,EXPLORER_SHA256_MAX_BYTES};
  Object.assign(surface,exported);if (typeof module !== "undefined" && module.exports) module.exports = exported;
})(typeof globalThis === "undefined" ? window : globalThis);
