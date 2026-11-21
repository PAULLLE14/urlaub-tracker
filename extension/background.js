// Schickt die vom Inhalts-Skript gemeldeten Preise an den eigenen Tracker. Lautlos: Fehler
// werden verschluckt, es gibt weder Meldungen noch eine Oberflaeche.
importScripts("config.js");

chrome.runtime.onMessage.addListener((msg) => {
  if (!msg || msg.type !== "prices") return;
  fetch(self.TRACKER_CFG.url.replace(/\/$/, "") + "/api/ext/ingest", {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Ext-Token": self.TRACKER_CFG.token },
    body: JSON.stringify(msg.payload),
  }).catch(() => {});
});
