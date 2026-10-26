"use strict";
const test=require("node:test"),assert=require("node:assert/strict"),{createHash,randomBytes,webcrypto}=require("node:crypto");
const {explorerSha256Bytes,EXPLORER_SHA256_MAX_BYTES}=require("../00-control-center/src/explorer/static/sha256.js");
const digest=bytes=>createHash("sha256").update(bytes).digest("hex");
const vectors=[
  ["","e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"],
  ["abc","ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"],
  ["abcdbcdecdefdefgefghfghighijhijkijkljklmklmnlmnomnopnopq","248d6a61d20638b8e5c026930c3e6039a33ce45964ff2167f6ecedd419db06c1"],
  ["a".repeat(1000000),"cdc76e5c9914fb9281a1c7e284d73e67f1809a48a497200e046d39ccc7112cd0"],
];
test("FIPS180-4 SHA256 known empty/abc/multiblock/million-a vectors in LAN fallback and WebCrypto",async()=>{
  for(const [text,expected] of vectors) {const bytes=new TextEncoder().encode(text);assert.equal(await explorerSha256Bytes(bytes,{crypto:null}),expected);assert.equal(await explorerSha256Bytes(bytes,{crypto:webcrypto}),expected);}
});
test("random boundary and0..2MiB inputs match Node crypto and real WebCrypto",async()=>{
  for(const length of [0,1,31,55,56,63,64,65,119,120,127,128,129,1023,65535,65536,65537,1048576,2097152]) {const bytes=randomBytes(length),expected=digest(bytes);assert.equal(await explorerSha256Bytes(bytes,{crypto:{}}),expected);assert.equal(await explorerSha256Bytes(bytes,{crypto:webcrypto}),expected);}
});
test("exact view offsets/ArrayBuffer and maximum8MiB bytes retain identity",async()=>{
  const original=new Uint8Array([99,1,2,3,88]),view=original.subarray(1,4);assert.equal(await explorerSha256Bytes(view,{crypto:null}),digest(Buffer.from([1,2,3])));assert.equal(await explorerSha256Bytes(view.slice().buffer,{crypto:null}),digest(Buffer.from([1,2,3])));
  const maximum=new Uint8Array(EXPLORER_SHA256_MAX_BYTES);maximum[0]=123;maximum[maximum.length-1]=77;assert.equal(await explorerSha256Bytes(maximum,{crypto:null}),digest(maximum));
});
test("invalid/oversized input or widened budget is rejected before cryptographic provider",async()=>{
  let calls=0;const crypto={subtle:{digest:async()=>{calls++;assert.fail("invalid input reached provider");}}};
  for(const bytes of [null,undefined,"abc",[1,2,3],new Uint16Array(3),{byteLength:2},new Uint8Array(EXPLORER_SHA256_MAX_BYTES+1)])await assert.rejects(explorerSha256Bytes(bytes,{crypto}));
  for(const maxBytes of [-1,1.5,Infinity,NaN,"4",EXPLORER_SHA256_MAX_BYTES+1])await assert.rejects(explorerSha256Bytes(new Uint8Array(),{crypto,maxBytes}));
  await assert.rejects(explorerSha256Bytes(new Uint8Array(2),{crypto,maxBytes:1}));assert.equal(calls,0);
});
test("snapshot copied before async digest; subsequent caller mutation cannot alter hash",async()=>{
  const bytes=Buffer.from("abc"),pending=explorerSha256Bytes(bytes,{crypto:webcrypto});bytes.fill(0);assert.equal(await pending,vectors[1][1]);
});
test("available WebCrypto is used and malformed provider digest fails closed",async()=>{
  let calls=0;const crypto={subtle:{digest:async(name,bytes)=>{calls++;assert.equal(name,"SHA-256");return webcrypto.subtle.digest(name,bytes);}}};assert.equal(await explorerSha256Bytes(new TextEncoder().encode("abc"),{crypto}),vectors[1][1]);assert.equal(calls,1);await assert.rejects(explorerSha256Bytes(new Uint8Array(),{crypto:{subtle:{digest:async()=>new Uint8Array(3)}}}),/invalid digest/);
});
