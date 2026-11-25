var TARGET = "";
var STACK = false; // false=新购，true=续费叠加到现有订阅（ReplacementMode=5 CHARGE_FULL_PRICE）
var ADDON = "";

var PLANS = {
  pro_12m:    { sku: "g1.2tb.ai.subs_variant", offer: "12months-eft",        note: "Pro 5TB 免费12个月，之后 $19.99/月" },
  pro_6m:     { sku: "g1.2tb.ai.subs_variant", offer: "6months-eft",         note: "Pro 5TB 免费6个月，之后 $19.99/月" },
  pro_6m_2:   { sku: "g1.2tb.ai.subs_variant", offer: "6months-eft-2",       note: "Pro 5TB 免费6个月（第二档，可叠），之后 $19.99/月" },
  pro_6m_ss:  { sku: "g1.2tb.ai.subs_variant", offer: "6months-eft-ss-2026", note: "Pro 5TB 免费6个月（SS 2026），之后 $19.99/月" },
  pro_play_3m:{ sku: "g1.play.2tb.ai",            offer: "eft-3mo",      note: "Pro 5TB 免费3个月，之后 $19.99/月" },
  plus_6m:    { sku: "g1.2tb.subs_variant", offer: "6months-eft", note: "Plus 2TB 免费6个月，之后 $9.99/月" },
  plus_3m:    { sku: "g1.2tb.subs_variant", offer: "3months-eft", note: "Plus 2TB 免费3个月，之后 $9.99/月" },
  plus_2tb_6m:{ sku: "g1.2tb.6months_eft",  offer: "freetrial",   note: "Plus 2TB 免费6个月，之后 $9.99/月" },
  helium_12m: { sku: "g1.play.helium", offer: "eft-12mo", note: "Plus 400GB 免费12个月，之后 $4.99/月" },
  helium_6m:  { sku: "g1.play.helium", offer: "eft-6mo",  note: "Plus 400GB 免费6个月，之后 $4.99/月" },
  helium_3m:  { sku: "g1.play.helium", offer: "eft-3mo",  note: "Plus 400GB 免费3个月，之后 $4.99/月" },
  helium_1m:  { sku: "g1.play.helium", offer: "eft-1mo",  note: "Plus 400GB 免费1个月，之后 $4.99/月" },
  yt_1m:      { sku: "g1.youtube_premium_individual_add_on", offer: "eft-1mo", note: "YouTube Premium 个人版 免费1个月，之后 $11.99/月" },
  yt_family:  { sku: "g1.youtube_premium_family_add_on",     offer: "eft-1mo", note: "YouTube Premium 家庭版 免费1个月" },
  yt_lite:    { sku: "g1.youtube_premium_lite_add_on",       offer: "eft-1mo", note: "YouTube Premium Lite 免费1个月" },
  helium_3m_75:  { sku: "g1.helium.3months_ip_75p.acq", offer: "introprice", note: "$1.29 Plus 400GB 3个月，之后 $4.99/月" },
  plus_2tb_85:   { sku: "g1.2tb.3months_ip_85p.acq",    offer: "introprice", note: "$1.49 Plus 2TB 3个月，之后 $9.99/月" },
  pro_3m_75:     { sku: "g1.2tb.ai.3months_ip_75p.acq", offer: "introprice", note: "$4.99 Pro 5TB 3个月，之后 $19.99/月" },
  pro10_1m_90:   { sku: "g1.10tb.1month_ip_90p",        offer: "introprice", note: "$4.99 Pro 10TB 1个月，之后 $49.99/月" },
  pro10_3m_85:   { sku: "g1.10tb.3months_ip_85p",       offer: "introprice", note: "$7.49 Pro 10TB 3个月，之后 $49.99/月" },
  pro10_base:    { sku: "g1.10tb",                      offer: "",           note: "Pro 10TB Play base (full price, no promo)" },
  ultra20_1m_eft:{ sku: "g1.20tb.1month_eft.acq",     offer: "freetrial",  note: "Ultra 5x（20TB）免费1个月，之后 $99.99/月" },
  ultra20_3m_90: { sku: "g1.20tb.3months_ip_90p.acq",   offer: "introprice", note: "$9.99 Ultra 5x（20TB）3个月，之后 $99.99/月" },
  ultra20_3m_80: { sku: "g1.20tb.3months_ip_80p.acq",   offer: "introprice", note: "$19.99 Ultra 5x（20TB）3个月，之后 $99.99/月" },
  ultra20_3m_75: { sku: "g1.20tb.3months_ip_75p.acq",   offer: "introprice", note: "$24.99 Ultra 5x（20TB）3个月，之后 $99.99/月" },
  ultra20_base:  { sku: "g1.play.20tb",                 offer: "",           note: "Ultra 5x (20TB) Play base (full price, no promo)" },
  ultra_1m_eft:  { sku: "g1.ai.ultra.1month_eft.acq",   offer: "freetrial",  note: "Ultra 20x（30TB）免费1个月，之后 $199.99/月" },
  ultra_1m_90:   { sku: "g1.ai.ultra.1month_ip_90p.acq", offer: "introprice", note: "$19.99 Ultra 20x（30TB）1个月，之后 $199.99/月" },
  ultra_3m_90:   { sku: "g1.ai.ultra.3months_ip_90p.acq", offer: "introprice", note: "$19.99 Ultra 20x（30TB）3个月，之后 $199.99/月" },
  ultra_3m_80:   { sku: "g1.ai.ultra.3months_ip_80p.acq", offer: "introprice", note: "$39.99 Ultra 20x（30TB）3个月，之后 $199.99/月" },
  ultra_1m_75:   { sku: "g1.ai.ultra.1month_ip_75p.acq", offer: "introprice", note: "$49.99 Ultra 20x（30TB）1个月，之后 $199.99/月" },
  ultra_3m_75:   { sku: "g1.ai.ultra.3months_ip_75p.acq", offer: "introprice", note: "$49.99 Ultra 20x（30TB）3个月，之后 $199.99/月" },
  ultra_30tb:    { sku: "g1.ai.pro",                    offer: "",           note: "Ultra 20x (30TB) Play base (full price, no promo)" }
};

var STACK_MODE = 5; // CHARGE_FULL_PRICE，叠加必须走这个
var DEX_PATHS = [
  "/data/local/tmp/module_521.apk",
  "/data/local/tmp/billing.apk"
];

var dexLoader = null;
var ctxRef = null;
var clientRef = null;
var booted = false;
var lastPurchaseToken = "";
var manualOldToken = "";
var held = [];
var clsSeq = 0;
function hold(x) { held.push(x); return x; }
function clsName(prefix) { return prefix + Date.now() + "_" + (++clsSeq); }

function L(v) { Java.classFactory.loader = v; }
function clip(x) { try { var s = String(x); return s.length > 800 ? s.slice(0, 800) : s; } catch (e) { return "?"; } }
function keys() { var a = []; for (var k in PLANS) a.push(k); return a; }
function onJava(fn) {
  Java.perform(fn);
}
function planList() {
  var a = [];
  for (var k in PLANS) a.push({ id: k, sku: PLANS[k].sku, offer: PLANS[k].offer, note: PLANS[k].note });
  return a;
}
var LOG_CONSOLE = false;
function log(ev, extra) {
  var o = extra || {};
  o.ev = ev;
  send(o);
  if (!LOG_CONSOLE) return;
  var bits = [ev];
  for (var k in extra) {
    if (k === "ev") continue;
    var v = extra[k];
    if (v === undefined || v === null || v === "") continue;
    bits.push(k + "=" + v);
  }
  console.log("[g1] " + bits.join("  "));
}

function getActivity() {
  L(null);
  var found = null, g1 = null;
  try {
    var th = Java.use("android.app.ActivityThread").currentActivityThread();
    var it = th.mActivities.value.values().iterator();
    while (it.hasNext()) {
      var rec = it.next();
      try {
        var act = rec.activity.value;
        if (!act || act.isFinishing()) continue;
        try { if (act.isDestroyed()) continue; } catch (e1) {}
        var paused = false;
        try { paused = rec.paused.value; } catch (e2) {}
        var name = String(act.getClass().getName());
        if (name.indexOf("subscriptions.red") >= 0) {
          g1 = act;
          if (!paused) found = act;
        }
        if (!found && !paused) found = act;
      } catch (e3) {}
    }
  } catch (e) { log("act_err", { m: clip(e) }); }
  if (!found) found = g1;
  if (!found) {
    try {
      Java.choose("android.app.Activity", {
        onMatch: function (a) { try { if (!found && !a.isFinishing()) found = a; } catch (e4) {} },
        onComplete: function () {}
      });
    } catch (e5) {}
  }
  return found;
}

function planOf(name) {
  var p = PLANS[name];
  if (!p) return null;
  var fb = [];
  if (typeof p.fallback === "string" && p.fallback) fb = [p.fallback];
  else if (Array.isArray(p.fallback)) fb = p.fallback;
  return { name: name, sku: p.sku, offer: p.offer, note: p.note, fallback: fb };
}

function isG1Sub(prods) {
  var s = String(prods || "");
  return s.indexOf("g1.") >= 0;
}

function pickOldToken(list) {
  var any = "", prefer = "";
  if (!list) return "";
  var Purchase = Java.use("com.android.billingclient.api.Purchase");
  for (var i = 0; i < list.size(); i++) {
    var pu = Java.cast(list.get(i), Purchase);
    var prods = String(pu.getProducts());
    var t = String(pu.getPurchaseToken() || "");
    log("old_purchase", { i: i, products: prods, head: t.slice(0, 16) });
    if (!t) continue;
    if (!any) any = t;
    if (isG1Sub(prods)) prefer = t;
  }
  return prefer || any;
}

function loadBilling() {
  L(null);
  ctxRef = Java.use("android.app.ActivityThread").currentApplication().getApplicationContext();
  var DexClassLoader = Java.use("dalvik.system.DexClassLoader");
  var cache = ctxRef.getCodeCacheDir().getAbsolutePath();
  var File = Java.use("java.io.File");
  for (var i = 0; i < DEX_PATHS.length; i++) {
    try {
      if (!File.$new(DEX_PATHS[i]).exists()) continue;
      dexLoader = DexClassLoader.$new(DEX_PATHS[i], cache, null, ctxRef.getClassLoader());
      L(dexLoader);
      Java.use("com.android.billingclient.api.BillingClient");
      log("dex", { path: DEX_PATHS[i] });
      return true;
    } catch (e) {
      dexLoader = null;
      L(null);
    }
  }
  var found = null;
  Java.enumerateClassLoaders({
    onMatch: function (loader) {
      if (found) return;
      try {
        if (loader.loadClass("com.android.billingclient.api.BillingClient")) found = loader;
      } catch (e) {}
    },
    onComplete: function () {}
  });
  if (found) {
    dexLoader = found;
    L(dexLoader);
    log("dex", { path: "app-loader" });
    return true;
  }
  log("boot_err", { m: "BillingClient not found. adb push module_521.apk /data/local/tmp/" });
  return false;
}

function connectThen(fn) {
  L(dexLoader);
  var BillingClient = Java.use("com.android.billingclient.api.BillingClient");
  var PUL = Java.use("com.android.billingclient.api.PurchasesUpdatedListener");
  var Lst = Java.registerClass({
    name: clsName("rc.PPA"),
    implements: [PUL],
    methods: {
      onPurchasesUpdated: function (r, p) {
        var code = r.getResponseCode();
        if (code === 0 && p && p.size() > 0) {
          try {
            L(dexLoader);
            var freshTok = pickOldToken(p);
            if (freshTok) lastPurchaseToken = freshTok;
          } catch (ep) {}
        }
        log("purchase_cb", { code: code, msg: String(r.getDebugMessage() || ""), n: p ? p.size() : 0 });
      }
    }
  });
  var b = BillingClient.newBuilder(ctxRef);
  b.setListener(hold(Lst.$new()));
  hold(Lst);
  try { b.enablePendingPurchases(); } catch (e) {}
  try {
    var PPP = Java.use("com.android.billingclient.api.PendingPurchasesParams");
    b.enablePendingPurchases(PPP.newBuilder().enableOneTimeProducts().build());
  } catch (e2) {}
  clientRef = b.build();
  var SLL = Java.use("com.android.billingclient.api.BillingClientStateListener");
  var SL = Java.registerClass({
    name: clsName("rc.PPB"),
    implements: [SLL],
    methods: {
      onBillingSetupFinished: function (r) {
        log("setup", { code: r.getResponseCode(), msg: String(r.getDebugMessage() || "") });
        if (r.getResponseCode() === 0) fn();
      },
      onBillingServiceDisconnected: function () { log("disc", {}); }
    }
  });
  hold(SL);
  clientRef.startConnection(hold(SL.$new()));
}

function applyStackParams(builder, oldTok) {
  var SUP = Java.use("com.android.billingclient.api.BillingFlowParams$SubscriptionUpdateParams");
  var sb = SUP.newBuilder();
  var modeSet = false;
  try { sb = sb.setReplacementMode(STACK_MODE); modeSet = true; } catch (e0) {}
  if (!modeSet) {
    try { sb = sb.setReplaceProrationMode(STACK_MODE); modeSet = true; } catch (e1) {}
  }
  if (!modeSet) {
    try { sb = sb.setReplaceSkusProrationMode(STACK_MODE); modeSet = true; } catch (e2) {
      log("mode_err", { m: clip(e2) });
    }
  }
  log("mode_applied", { mode: STACK_MODE, ok: modeSet });
  builder = builder.setSubscriptionUpdateParams(sb.setOldPurchaseToken(String(oldTok)).build());
  log("stack_mode", { mode: STACK_MODE, old: String(oldTok).slice(0, 16) });
  return builder;
}

function launchFlow(hit, hitOffer, add, addOffer, oldTok, spec) {
  L(dexLoader);
  var PDP = Java.use("com.android.billingclient.api.BillingFlowParams$ProductDetailsParams");
  var BFP = Java.use("com.android.billingclient.api.BillingFlowParams");
  var l = Java.use("java.util.ArrayList").$new();
  var tok = String(hitOffer.getOfferToken());
  log("use_token", {
    n: tok.length,
    sku: spec.sku,
    offer: spec.offer,
    target: spec.name,
    addon: ADDON || "",
    stack: !!oldTok
  });
  l.add(PDP.newBuilder().setProductDetails(hit).setOfferToken(tok).build());
  if (add && addOffer && String(add.getProductId()) !== spec.sku) {
    l.add(PDP.newBuilder().setProductDetails(add).setOfferToken(String(addOffer.getOfferToken())).build());
    log("addon", { sku: String(add.getProductId()), offer: String(addOffer.getOfferId() || "") });
  }
  var b = BFP.newBuilder().setProductDetailsParamsList(l);
  if (oldTok) {
    try { b = applyStackParams(b, oldTok); } catch (e) { log("stack_param_err", { m: clip(e) }); }
  }
  var params = b.build();
  L(null);
  var Handler = Java.use("android.os.Handler");
  var Looper = Java.use("android.os.Looper");
  var tries = [0];
  var R = Java.registerClass({
    name: clsName("rc.PPR"),
    implements: [Java.use("java.lang.Runnable")],
    methods: {
      run: function () {
        try {
          var act = getActivity();
          log("activity", { a: act ? String(act.getClass().getName()) : "null", try: tries[0] });
          if (!act) {
            if (tries[0] < 8) {
              tries[0] += 1;
              Handler.$new(Looper.getMainLooper()).postDelayed(R.$new(), 400);
              return;
            }
            log("no_activity", {});
            return;
          }
          L(dexLoader);
          var Activity = Java.use("android.app.Activity");
          var r2 = clientRef.launchBillingFlow(Java.cast(act, Activity), params);
          log("launched", { code: r2.getResponseCode(), msg: String(r2.getDebugMessage() || "") });
        } catch (e) { log("launch_err", { m: clip(e) }); }
      }
    }
  });
  hold(R);
  Handler.$new(Looper.getMainLooper()).post(hold(R.$new()));
}

function shOut(argv) {
  L(null);
  try {
    var ArrayList = Java.use("java.util.ArrayList");
    var args = ArrayList.$new();
    for (var i = 0; i < argv.length; i++) args.add(argv[i]);
    var pb = Java.use("java.lang.ProcessBuilder").$new(args);
    pb.redirectErrorStream(true);
    var proc = pb.start();
    var br = Java.use("java.io.BufferedReader").$new(Java.use("java.io.InputStreamReader").$new(proc.getInputStream()));
    var out = "";
    var line;
    while (true) {
      line = br.readLine();
      if (line === null) break;
      out += String(line) + "\n";
    }
    proc.waitFor();
    return out;
  } finally {
    L(dexLoader);
  }
}

function tokenFromPlayDb() {
  try {
    var sql = "select inapp_purchase_data from ownership where doc_id like '%g1.%' or doc_id like '%subs:com.google.android.apps.subscriptions.red:%' order by subs_valid_until_time desc limit 5;";
    var out = shOut(["su", "0", "sqlite3", "/data/data/com.android.vending/databases/library.db", sql]);
    var m = out.match(/"purchaseToken"\s*:\s*"([^"]+)"/);
    if (m && m[1]) {
      log("old_purchase", { i: 0, products: "library.db", head: m[1].slice(0, 16) });
      return m[1];
    }
    log("library_miss", { n: out.length });
  } catch (e) {
    var msg = clip(e);
    if (msg.indexOf("Permission denied") < 0) log("library_err", { m: msg });
  }
  return "";
}

function queryPurchasesAsyncList(cb) {
  L(dexLoader);
  var PRL = Java.use("com.android.billingclient.api.PurchasesResponseListener");
  var cls = hold(Java.registerClass({
    name: clsName("rc.PPS"),
    implements: [PRL],
    methods: {
      onQueryPurchasesResponse: function (br, purchases) {
        try {
          L(dexLoader);
          var list2 = Java.cast(purchases, Java.use("java.util.List"));
          var code = br.getResponseCode();
          log("purchases", { n: list2.size(), code: String(code) });
          cb(code, list2);
        } catch (e2) {
          log("stack_query_err", { m: clip(e2) });
          cb(-1, null);
        }
      }
    }
  }));
  var listener = hold(cls.$new());
  var Handler = Java.use("android.os.Handler");
  var Looper = Java.use("android.os.Looper");
  var Rq = hold(Java.registerClass({
    name: clsName("rc.PPQ"),
    implements: [Java.use("java.lang.Runnable")],
    methods: {
      run: function () {
        try {
          var QP = Java.use("com.android.billingclient.api.QueryPurchasesParams");
          clientRef.queryPurchasesAsync(QP.newBuilder().setProductType("subs").build(), listener);
          log("purchases_async", { via: "params-main" });
        } catch (e3) {
          try {
            clientRef.queryPurchasesAsync("subs", listener);
            log("purchases_async", { via: "skuType-main" });
          } catch (e4) {
            log("purchases_sku_err", { m: clip(e4) });
            cb(-1, null);
          }
        }
      }
    }
  }));
  Handler.$new(Looper.getMainLooper()).post(hold(Rq.$new()));
}

function queryPurchasesThen(cb) {
  L(dexLoader);
  if (manualOldToken) {
    log("old_purchase", { i: 0, products: "host-db", head: manualOldToken.slice(0, 16) });
    cb(manualOldToken);
    return;
  }
  var dbTok = tokenFromPlayDb();
  if (dbTok) {
    lastPurchaseToken = dbTok;
    cb(dbTok);
    return;
  }
  queryPurchasesAsyncList(function (_code, list2) {
    var tok = pickOldToken(list2) || lastPurchaseToken;
    if (tok) lastPurchaseToken = tok;
    cb(tok);
  });
}

function checkSubsNow() {
  if (!clientRef) {
    log("subs_check", { code: -1, n: 0, has_g1: false, products: "" });
    return;
  }
  queryPurchasesAsyncList(function (code, list2) {
    var hasG1 = false;
    var prodsArr = [];
    if (list2) {
      L(dexLoader);
      var Purchase = Java.use("com.android.billingclient.api.Purchase");
      for (var i = 0; i < list2.size(); i++) {
        var pu = Java.cast(list2.get(i), Purchase);
        var prods = String(pu.getProducts());
        prodsArr.push(prods);
        if (isG1Sub(prods)) hasG1 = true;
      }
    }
    log("subs_check", { code: code, n: list2 ? list2.size() : 0, has_g1: hasG1, products: prodsArr.join(",") });
  });
}

function queryProductDetailsForSkus(skus, cb) {
  L(dexLoader);
  var Prod = Java.use("com.android.billingclient.api.QueryProductDetailsParams$Product");
  var Params = Java.use("com.android.billingclient.api.QueryProductDetailsParams");
  var PDL = Java.use("com.android.billingclient.api.ProductDetailsResponseListener");
  var PL = Java.registerClass({
    name: clsName("rc.PPC"),
    implements: [PDL],
    methods: {
      onProductDetailsResponse: function (res, list) {
        try {
          L(dexLoader);
          cb(res, list);
        } catch (e) {
          log("pd_cb_err", { m: clip(e) });
        }
      }
    }
  });
  var arr = Java.use("java.util.ArrayList").$new();
  var seen = {};
  for (var i = 0; i < skus.length; i++) {
    var sku = skus[i];
    if (!sku || seen[sku]) continue;
    seen[sku] = 1;
    arr.add(Prod.newBuilder().setProductId(sku).setProductType("subs").build());
  }
  hold(PL);
  clientRef.queryProductDetailsAsync(Params.newBuilder().setProductList(arr).build(), hold(PL.$new()));
  return arr.size();
}

function formatOfferPrice(o) {
  try {
    var PricingPhase = Java.use("com.android.billingclient.api.ProductDetails$PricingPhase");
    var phases = o.getPricingPhases().getPricingPhaseList();
    var parts = [];
    for (var p = 0; p < phases.size(); p++) {
      var ph = Java.cast(phases.get(p), PricingPhase);
      parts.push(String(ph.getFormattedPrice()));
    }
    return parts.join(" -> ");
  } catch (ep) {
    return "";
  }
}

function collectProductDetails(list) {
  var items = [];
  var byKey = {};
  if (!list) return { items: items, byKey: byKey };
  L(dexLoader);
  var PD = Java.use("com.android.billingclient.api.ProductDetails");
  var SOD = Java.use("com.android.billingclient.api.ProductDetails$SubscriptionOfferDetails");
  for (var i = 0; i < list.size(); i++) {
    var pd = Java.cast(list.get(i), PD);
    var pid = String(pd.getProductId());
    var title = String(pd.getTitle() || "");
    var offs = pd.getSubscriptionOfferDetails();
    if (!offs) continue;
    for (var j = 0; j < offs.size(); j++) {
      var o = Java.cast(offs.get(j), SOD);
      var oid = String(o.getOfferId() || "");
      var price = formatOfferPrice(o);
      var key = pid + "|" + oid;
      byKey[key] = { pd: pd, offerObj: o };
      items.push({ sku: pid, offer: oid, title: title, price: price });
    }
  }
  return { items: items, byKey: byKey };
}

function queryOfferThen(spec, oldTok) {
  var addon = ADDON && PLANS[ADDON] ? PLANS[ADDON] : null;
  var fbSpecs = [];
  for (var f = 0; f < (spec.fallback || []).length; f++) {
    var fs = planOf(spec.fallback[f]);
    if (fs) fbSpecs.push(fs);
  }
  var skus = [spec.sku];
  for (var k = 0; k < fbSpecs.length; k++) skus.push(fbSpecs[k].sku);
  if (addon) skus.push(addon.sku);

  var count = queryProductDetailsForSkus(skus, function (res, list) {
    var rcode = res.getResponseCode();
    log("pd", { code: rcode, n: list ? list.size() : 0, msg: String(res.getDebugMessage() || "") });
    if (rcode !== 0 || !list) { log("done", {}); return; }
    var parsed = collectProductDetails(list);
    if (parsed.items.length > 0) {
      log("sku_seen", { source: "query_offer", n: parsed.items.length, items: JSON.stringify(parsed.items) });
    }
    var chosen = spec;
    var pair = parsed.byKey[spec.sku + "|" + spec.offer];
    if (!pair && spec.offer === "freetrial") {
      for (var ii = 0; ii < parsed.items.length; ii++) {
        var it = parsed.items[ii];
        if (it.sku === spec.sku && it.offer && /trial|eft/i.test(it.offer)) {
          pair = parsed.byKey[it.sku + "|" + it.offer];
          chosen = { name: spec.name, sku: spec.sku, offer: it.offer, note: spec.note, fallback: spec.fallback };
          break;
        }
      }
    }
    if (!pair) {
      for (var fi = 0; fi < fbSpecs.length; fi++) {
        var fb = fbSpecs[fi];
        var fbPair = parsed.byKey[fb.sku + "|" + fb.offer];
        if (fbPair) {
          pair = fbPair;
          chosen = fb;
          log("fallback_hit", { from: spec.name, to: fb.name, sku: fb.sku, offer: fb.offer });
          break;
        }
      }
    }
    if (!pair) { log("no_offer", { sku: spec.sku, offer: spec.offer, target: spec.name }); return; }
    var addPair = addon ? parsed.byKey[addon.sku + "|" + addon.offer] : null;
    if (ADDON && !addPair) log("no_addon", { addon: ADDON });
    if (oldTok) log("stack_start", { src: "old_token", sku: chosen.sku, offer: chosen.offer });
    launchFlow(pair.pd, pair.offerObj, addPair ? addPair.pd : null, addPair ? addPair.offerObj : null, oldTok || "", chosen);
  });
  log("query_sent", { n: count, sku: spec.sku, offer: spec.offer });
}

function checkOffersNow() {
  if (!clientRef) {
    log("offers_audit", { code: -1, items: "[]" });
    return;
  }
  var skus = [];
  for (var k in PLANS) skus.push(PLANS[k].sku);
  queryProductDetailsForSkus(skus, function (res, list) {
    var code = res.getResponseCode();
    var items = code === 0 && list ? collectProductDetails(list).items : [];
    if (items.length > 0) {
      log("sku_seen", { source: "check_offers", n: items.length, items: JSON.stringify(items) });
    }
    log("offers_audit", { code: code, n: items.length, items: JSON.stringify(items) });
  });
}

function run(target, stack) {
  var spec = planOf(target);
  if (!spec) { log("bad_target", { target: target, keys: keys().join(",") }); return; }
  TARGET = spec.name;
  STACK = !!stack;
  log("perform", {
    target: spec.name, sku: spec.sku, offer: spec.offer, note: spec.note,
    stack: STACK, mode: STACK ? "renew" : "new"
  });
  if (!clientRef) {
    log("boot_err", { m: "BillingClient not ready" });
    return;
  }
  if (STACK) {
    queryPurchasesThen(function (tok) {
      if (!tok) { log("no_purchase", { m: "当前账号没有可叠加的订阅" }); return; }
      queryOfferThen(spec, tok);
    });
  } else {
    queryOfferThen(spec, "");
  }
}

function boot() {
  if (booted && clientRef) {
    log("ready", { plans: keys().length, reused: true });
    return;
  }
  if (!loadBilling()) return;
  connectThen(function () {
    booted = true;
    log("ready", { plans: keys().length, reused: false });
  });
}

rpc.exports = {
  plans: function () { return planList(); },
  status: function () {
    return { booted: booted, target: TARGET, stack: STACK, addon: ADDON };
  },
  setAddon: function (name) {
    ADDON = name || "";
    log("addon_set", { addon: ADDON });
    return ADDON;
  },
  setOldToken: function (tok) {
    manualOldToken = tok || "";
    return manualOldToken ? manualOldToken.slice(0, 12) : "";
  },
  buy: function (name) {
    var t = name || TARGET;
    onJava(function () { run(t, false); });
    return "buy " + t;
  },
  stack: function (name) {
    var t = name || TARGET;
    onJava(function () { run(t, true); });
    return "stack " + t;
  },
  setPlans: function (jsonStr) {
    PLANS = JSON.parse(jsonStr);
    return keys().length;
  },
  checkSubs: function () {
    onJava(function () { checkSubsNow(); });
    return "checkSubs";
  },
  checkOffers: function () {
    onJava(function () { checkOffersNow(); });
    return "checkOffers";
  }
};

recv("cfg", function onCfg(msg) {
  var p = (msg && msg.payload) ? msg.payload : (msg || {});
  TARGET = p.target || TARGET;
  STACK = !!p.stack;
  if (p.addon !== undefined) ADDON = p.addon || "";
  if (p.oldToken !== undefined) manualOldToken = p.oldToken || "";
  onJava(function () { run(TARGET, STACK); });
  recv("cfg", onCfg);
});

var scriptStarted = false;
setImmediate(function () {
  onJava(function () {
    if (scriptStarted) return;
    scriptStarted = true;
    log("script_start", { plans: keys().length });
    boot();
  });
});
