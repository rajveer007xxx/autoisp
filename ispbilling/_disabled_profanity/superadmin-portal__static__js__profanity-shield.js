/* _S40zμ_  Client-side profanity shield
   ────────────────────────────────────────────────────────────────
   • Listens on every form `submit`, `keydown` (Enter), and intercepts
     fetch / XMLHttpRequest / jQuery.ajax payloads.
   • Roman words match with \b word-boundaries; Devanagari uses
     Unicode-aware lookarounds against the U+0900–U+097F block.
   • On detection, blocks the action and shows an alert that warns
     the user their IP is being traced. */
(function () {
  if (window.__PROFANITY_SHIELD__) return;
  window.__PROFANITY_SHIELD__ = true;

  const ROMAN = ["aad","aand","b.c.","b.s.d.k","babbe","babbey","bahenchod","bakchod","bakchodd","bakchodi","bc","behenchod","bevakoof","bevda","bevdey","bevkoof","bevkuf","bewakoof","bewday","bewda","bewkoof","bewkuf","bhadua","bhaduaa","bhadva","bhadvaa","bhadwa","bhadwaa","bhenchod","bhenchodd","bhonsdike","bhosada","bhosadchod","bhosadchodal","bhosda","bhosdaa","bhosdike","bhosdiki","bhosdiwala","bhosdiwale","bsdk","bube","bubey","bur","burr","buur","buurr","charsi","chhod","chod","chodd","chooche","choochi","choot","chuchi","chudne","chudney","chudwa","chudwaa","chudwaane","chudwane","chut","chutad","chute","chutia","chutiya","chutiye","chuttad","dalaal","dalal","dalle","dalley","fattu","gaand","gadha","gadhalund","gadhe","gand","gandfat","gandfut","gandiya","gandiye","gandu","goo","gote","gotey","gotte","gu","hag","haggu","hagne","hagney","haraamjaada","haraamjaade","haraamkhor","haraamzaade","haraamzyaada","haramjada","haramkhor","haramzyada","harami","jhaat","jhaatu","jhat","jhatu","kutia","kutiya","kutta","kutte","kutti","kuttey","kuttiya","landi","landy","launda","laundey","laundi","laundiya","lauda","laude","laudey","laura","ling","loda","lode","lora","lounde","loundi","loundiya","lulli","lund","m.c.","maar","madarchod","madarchodd","madarchood","madarchoot","madarchut","mamme","mammey","maro","marunga","mc","moot","mooth","mootne","mut","muth","mutne","nunni","nunnu","paaji","paji","pesaab","pesab","peshaab","peshab","pilla","pillay","pille","pilley","pisaab","pisab","pkmkb","porkistan","raand","rand","randi","randy","suar","tatte","tatti","tatty","ullu"];

  const DEVA = ["आंड","आंड़","आँड","उल्लू","कुतिया","कुत्ता","कुत्ती","कुत्ते","गंडफट","गंडिया","गंडिये","गधा","गधालंड","गधे","गांड","गांडू","गू","गोटे","चरसी","चुची","चुटिया","चुत्तड़","चुदने","चुदवा","चुदवाने","चूचे","चूची","चूत","चूत्तड़","चूतिया","चूतिये","चोद","झाट","झाटू","टट्टी","टट्टे","दलले","दलाल","नुननी","नुननु","पाजी","पिल्ला","पिल्ले","पिसाब","पेसाब","पेशाब","पोरकिस्तान","फट्टू","बकचोद","बकचोदी","बब्बे","बहनचोद","बुर","बूबे","बेवड़ा","बेवड़े","बेवकूफ","बेहेनचोद","भड़ुआ","भड़वा","भेनचोद","भोसड़ा","भोसड़ाचोद","भोसड़ाचोदल","भोसड़ीकी","भोसड़ीके","भोसड़ीवाला","भोसड़ीवाले","भोसदचोद","भोसरचोदल","मम्मे","मादरचुत","मादरचूत","मादरचोद","मार","मारूंगा","मारो","मुठ","मुत","मुतने","मूठ","मूत","मूतने","रंडी","रांड","लंड","लिंग","लुल्ली","लेंडी","लोडा","लोडे","लोड़ा","लोड़े","लौंडा","लौंडिया","लौंडी","लौंडे","लौड़ा","लौड़े","लौडा","सुअर","सूअर","हग","हग्गू","हगने","हरामखोर","हरामज़ादा","हरामज़ादे","हरामजादा","हरामजादे","हरामी"];

  const reEsc = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const RX_ROMAN = new RegExp("\\b(" + ROMAN.map(reEsc).join("|") + ")\\b", "i");
  const RX_DEVA  = new RegExp("(?<![\\u0900-\\u097F])(" + DEVA.map(reEsc).join("|") + ")(?![\\u0900-\\u097F])");

  function findProfanity(text) {
    if (!text || typeof text !== "string") return null;
    let m = text.match(RX_ROMAN); if (m) return m[1];
    m = text.match(RX_DEVA);     if (m) return m[1];
    return null;
  }
  window.__findProfanity = findProfanity;

  // ── Centralised alert ─────────────────────────────────────────
  let _alertOpen = false;
  function showProfanityAlert(word) {
    if (_alertOpen) return;
    _alertOpen = true;
    try {
      alert(
        "⚠ Inappropriate language detected: \"" + word + "\"\n\n" +
        "You cannot use offensive or abusive words on this portal.\n\n" +
        "Your IP address has been logged and an FIR may be lodged " +
        "under IT Act 2000 and IPC Sec 354A/509.\n\n" +
        "Please remove this word and try again."
      );
    } finally {
      setTimeout(() => { _alertOpen = false; }, 600);
    }
  }
  window.__showProfanityAlert = showProfanityAlert;

  // ── Scan a form's text inputs ─────────────────────────────────
  function scanForm(form) {
    if (!form || !form.querySelectorAll) return null;
    const sel = "input[type=text], input[type=search], input[type=email], "
              + "input[type=tel], input[type=url], input[type=password], "
              + "input:not([type]), textarea, [contenteditable=true]";
    const fields = form.querySelectorAll(sel);
    for (const f of fields) {
      const v = (f.value !== undefined) ? f.value : f.innerText;
      const hit = findProfanity(v);
      if (hit) return { word: hit, field: f };
    }
    return null;
  }

  // ── Form submit handler (capture phase, fires before site code) ─
  document.addEventListener("submit", function (e) {
    const form = e.target;
    if (!form || form.tagName !== "FORM") return;
    const r = scanForm(form);
    if (r) {
      e.preventDefault(); e.stopPropagation();
      showProfanityAlert(r.word);
      try { r.field.focus(); r.field.select && r.field.select(); } catch (_) {}
      return false;
    }
  }, true);

  // ── fetch() interceptor ────────────────────────────────────────
  const _origFetch = window.fetch;
  if (typeof _origFetch === "function") {
    window.fetch = function (input, init) {
      try {
        const m = ((init && init.method) || "GET").toUpperCase();
        if (["POST", "PUT", "PATCH"].includes(m) && init && init.body) {
          let body = init.body;
          if (typeof body !== "string" && body && body.toString)
            body = body.toString();
          const hit = findProfanity(body);
          if (hit) {
            showProfanityAlert(hit);
            return Promise.reject(new Error(
              "Profanity blocked: " + hit));
          }
        }
      } catch (_) { /* let through */ }
      return _origFetch.apply(this, arguments);
    };
  }

  // ── XMLHttpRequest interceptor (covers $.ajax / axios) ─────────
  const _origSend = XMLHttpRequest.prototype.send;
  XMLHttpRequest.prototype.send = function (body) {
    try {
      if (body && (typeof body === "string" || body.toString)) {
        const text = (typeof body === "string") ? body : body.toString();
        const hit = findProfanity(text);
        if (hit) {
          showProfanityAlert(hit);
          // Cancel by aborting before send.
          try { this.abort(); } catch (_) {}
          return;
        }
      }
    } catch (_) { /* let through */ }
    return _origSend.apply(this, arguments);
  };

  // ── Listen for server-side blocks (from the FastAPI middleware)
  // and surface the alert if the JS path missed something.
  document.addEventListener("DOMContentLoaded", function () {
    const _origJqAjax = window.jQuery && window.jQuery.ajax;
    if (_origJqAjax) {
      window.jQuery(document).ajaxError(function (_e, xhr) {
        try {
          if (xhr && xhr.responseJSON && xhr.responseJSON.blocked)
            showProfanityAlert(xhr.responseJSON.offending_word
                                || "(profanity)");
        } catch (_) {}
      });
    }
  });
})();
