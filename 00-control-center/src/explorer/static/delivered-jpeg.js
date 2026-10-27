"use strict";
// ENGINEERING_GLUE: shared data-only JPEG bytes/identity verification; no permission or rendering.
(function (surface) {
  function deliveredImageVisible(image, root, {document,window} = {}) {
    if (!image || !root.contains(image) || image.isConnected === false || !image.complete || !image.naturalWidth || document?.visibilityState === "hidden") return false;
    if (image.getClientRects && !image.getClientRects().length) return false;
    if (image.getBoundingClientRect) {
      const rect=image.getBoundingClientRect();
      if (rect.width <= 0 || rect.height <= 0 || rect.bottom <= 0 || rect.right <= 0 || rect.top >= (window?.innerHeight ?? Infinity) || rect.left >= (window?.innerWidth ?? Infinity)) return false;
    }
    return true;
  }
  async function verifiedDeliveredJPEG(path, expected, options = {}) {
    if (typeof path !== "string" || !path.startsWith("/api/") || /[?#\\]/.test(path)) throw new Error("Нужен фиксированный исходный кадр приложения");
    let raw;
    if (options.loadFrame) raw = await options.loadFrame(path);
    else {
      const response = await (options.fetch || surface.fetch)(path,{credentials:"same-origin",cache:"no-store"});
      if (!response.ok) throw new Error("Исходный кадр недоступен: "+response.status);
      const size = Number(response.headers.get("content-length"));
      if (Number.isFinite(size) && size > 8*1024**2) throw new Error("Кадр превышает допустимый размер");
      raw = {bytes:new Uint8Array(await response.arrayBuffer()),headers:response.headers};
    }
    const bytes = raw.bytes instanceof Uint8Array ? raw.bytes : new Uint8Array(raw.bytes);
    if (bytes.length < 4 || bytes.length > 8*1024**2 || bytes[0] !== 255 || bytes[1] !== 216 || bytes.at(-2) !== 255 || bytes.at(-1) !== 217) throw new Error("Нужен настоящий ограниченный JPEG");
    const header = name=>raw.headers?.get ? raw.headers.get(name) : raw.headers?.[name.toLowerCase()];
    const checksum = header("x-jpeg-sha256"), frameID = header("x-frame-id"), boot = header("x-boot-id");
    const digest = options.sha256 || surface.explorerSha256Bytes || (typeof require === "function" ? require("./sha256.js").explorerSha256Bytes : null);
    if (typeof digest !== "function") throw new Error("Не загружен общий SHA256 verifier");
    const actual = await digest(bytes,{crypto:Object.hasOwn(options,"crypto") ? options.crypto : surface.crypto});
    if (!/^[0-9a-f]{64}$/.test(checksum || "") || checksum !== actual || !frameID || !boot ||
        checksum !== expected.jpeg_sha256 || frameID !== expected.frame_id || (expected.boot_id && boot !== expected.boot_id)) throw new Error("SHA, камера или boot исходного кадра не совпадают");
    return {bytes,jpeg_sha256:checksum,frame_id:frameID,boot_id:boot};
  }
  surface.verifiedDeliveredJPEG = verifiedDeliveredJPEG;
  surface.deliveredImageVisible = deliveredImageVisible;
  if (typeof module !== "undefined" && module.exports) module.exports = {verifiedDeliveredJPEG,deliveredImageVisible};
})(typeof window !== "undefined" ? window : globalThis);
