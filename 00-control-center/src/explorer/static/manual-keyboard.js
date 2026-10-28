"use strict";
// ENGINEERING_GLUE: browser client lifecycle for the unchanged native executor.
(function (surface) {
  class ManualKeyboard {
    constructor(options = {}) {
      this.options = options; this.active = false; this.paused = true; this.starting = false;
      this.busy = false; this.keys = new Set(); this.heldKeys = new Set(); this.mode = "cartesian"; this.speed = "normal";
      this.lastKeys = ""; this.error = null; this.epoch = 0; this.inflight = new Set(); this.stopping = null; this.stopUnconfirmed = false;
      this.selectionAttempted = false;
    }
    context(epoch) {
      if (epoch !== this.epoch || !this.options.ready()) throw new Error("Управление прервано. Нужны свежая камера и новое явное подключение.");
      if (this.options.ownershipCurrent && !this.options.ownershipCurrent(this.ownershipToken)) throw new Error("Выбран другой источник управления.");
    }
    packet(observing = true) {
      const keys = this.options.filterKeys ? this.options.filterKeys([...this.keys]) : [...this.keys];
      return { source: "keyboard", keys: observing ? keys.sort() : [], observing, arm_mode: this.mode };
    }
    async request(epoch, operation, body) {
      this.context(epoch);
      const pending = Promise.resolve().then(() => { this.context(epoch); if(operation==="select")this.selectionAttempted=true;return this.options.post("/api/robot/teleop/" + operation, body); });
      this.inflight.add(pending);
      try { const result = await pending; this.context(epoch); return result; }
      finally { this.inflight.delete(pending); }
    }
    async cleanup(disconnect) {
      const operations = [["input", this.packet(false)], ["stop", {}]];
      if (disconnect) operations.push(["disconnect", { source: "keyboard" }]);
      const results=[];
      for(const [op,body] of operations){try{results.push({status:"fulfilled",value:await this.options.post("/api/robot/teleop/"+op,body,{keepalive:true})});}catch(error){results.push({status:"rejected",reason:error});}}
      this.stopUnconfirmed = results.slice(1).some(result => result.status === "rejected");
      if(!this.stopUnconfirmed && disconnect)this.selectionAttempted=false;
      if (this.stopUnconfirmed) this.error = "Завершение управления не подтверждено. Повторите STOP перед подключением.";
      return results;
    }
    async stop(disconnect = true) {
      const owned = this.active || this.selectionAttempted || this.stopping || this.stopUnconfirmed;
      ++this.epoch; this.keys.clear(); this.paused = true;
      if (disconnect) this.active = false;
      if (!owned) return;
      if (!this.stopping) {
        const pending = [...this.inflight];
        this.stopping = (async () => {
          await this.cleanup(disconnect);
          if (pending.length) {
            this.active = false;
            await Promise.allSettled(pending);
            // A response arriving after STOP must not leave a late select/resume alive.
            await this.cleanup(true);
          }
        })();
      }
      try { await this.stopping; } finally { this.stopping = null; this.changed(); }
    }
    changed() { if (this.options.changed) this.options.changed(); }
    async start() {
      if (this.busy || this.starting || this.stopping) throw new Error("Предыдущее управление ещё завершается.");
      this.ownershipToken = this.options.claim ? this.options.claim() : null;
      this.starting = true; this.busy = true; const epoch = ++this.epoch;
      try {
        if (this.stopUnconfirmed) { await this.cleanup(true); if (this.stopUnconfirmed) throw new Error(this.error); }
        if (this.options.beforeConnect) await this.options.beforeConnect(this.ownershipToken);
        this.context(epoch);
        if (this.options.prepare) await this.options.prepare();
        this.context(epoch);
        if (this.heldKeys.size) throw new Error("Перед подключением отпустите клавиши движения.");
        this.keys.clear(); this.error = null;
        await this.request(epoch, "select", { source: "keyboard", observing: true, neutral: true });
        await this.request(epoch, "input", this.packet(true));
        await this.request(epoch, "resume", { source: "keyboard", observing: true });
        this.active = true; this.paused = false;
      } catch (error) { this.error = error.message; await this.stop(true); throw error; }
      finally { this.starting = false; this.busy = false; this.changed(); }
    }
    async send() {
      if (!this.active || this.paused || this.busy || this.stopping) return;
      this.busy = true; const epoch = this.epoch;
      try { const body = this.packet(); this.lastKeys = body.keys.join(","); await this.request(epoch, "input", body); }
      catch (error) { this.error = error.message; await this.stop(true); }
      finally { this.busy = false; }
    }
    async resume() {
      if (!this.active || !this.paused || this.busy || this.stopping || this.heldKeys.size) throw new Error("Отпустите клавиши и дождитесь завершения STOP.");
      this.busy = true; const epoch = this.epoch;
      try {
        await this.request(epoch, "input", this.packet(true));
        await this.request(epoch, "resume", { source: "keyboard", observing: true }); this.paused = false; this.error = null;
      } catch (error) { this.error = error.message; await this.stop(true); throw error; }
      finally { this.busy = false; this.changed(); }
    }
    async configure(operation, value) {
      if (!this.active || this.busy || this.stopping) return;
      if (operation === "mode" && !["cartesian", "joint"].includes(value)) return;
      if (operation === "speed" && !["normal", "fast"].includes(value)) return;
      const paused = this.paused, epoch = this.epoch; this.busy = true; this.paused = true; this.keys.clear();
      try {
        await this.request(epoch, "input", this.packet(true));
        await this.request(epoch, operation, { source: "keyboard", [operation]: value });
        this[operation] = value; this.paused = paused;
      } catch (error) { this.error = error.message; await this.stop(true); throw error; }
      finally { this.busy = false; this.changed(); }
    }
  }
  surface.ManualKeyboard = ManualKeyboard;
  if (typeof module !== "undefined" && module.exports) module.exports = { ManualKeyboard };
})(typeof window !== "undefined" ? window : globalThis);
