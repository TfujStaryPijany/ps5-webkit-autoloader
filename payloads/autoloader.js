// PS5 WebKit Autoloader: unified orchestrator.
//
// 1. Checks if an ELF loader daemon is already listening on loopback port 9021.
// 2. Checks if the process is already jailbroken (uid 0).
// 3. If exploit is needed, selects between relapse.lua and poops.js. The
//    second payload argument (set per entry page, e.g. poops.html/relapse.html
//    of the "dual" build) forces one exploit; otherwise it picks based on
//    firmware version and network status (matching ps5-lua-autoloader).
// 4. Pre-fetches all needed assets before kernel exploit runs.
// 5. Executes the chosen exploit via api.executePayload().
// 6. Executes autoload.js to stage and stream the target ELF to elfldr on 9021.

const NR = {
  SOCKET: 0x61,
  CONNECT: 0x62,
  CLOSE: 6,
  GETUID: 24,
};

const POOPS = {
  file: "payloads/poops.js",
  fallback: "poops.js",
  args: [],
};
const RELAPSE = {
  file: "payloads/lua/relapse.lua",
  fallback: "relapse.lua",
  args: ["compat=remote_lua_loader"],
};

function failed(val) {
  return (val.low | 0) === -1;
}

async function isElfldrRunning(api) {
  let fd = -1;
  try {
    const addr = api.p.malloc(16, 1);
    for (let i = 0; i < 16; i++) api.p.write1(addr.add32(i), 0);
    // sockaddr_in: length 16, AF_INET (2), port 9021 (0x233d), 127.0.0.1
    [16, 2, 0x23, 0x3d, 127, 0, 0, 1].forEach((v, i) => api.p.write1(addr.add32(i), v));

    const socket = await api.chain.syscall(NR.SOCKET, 2, 1, 0);
    if (failed(socket)) return false;
    fd = socket.low | 0;

    const connected = await api.chain.syscall(NR.CONNECT, fd, addr, 16);
    return (connected.low | 0) === 0;
  } catch {
    return false;
  } finally {
    if (fd >= 0) {
      try { await api.chain.syscall(NR.CLOSE, fd); } catch {}
    }
  }
}

async function isJailbroken(api) {
  try {
    const uid = await api.chain.syscall(NR.GETUID);
    return (uid.low | 0) === 0;
  } catch {
    return false;
  }
}

// "poops" / "relapse" from the entry page's payload args, else "" (auto).
function forcedExploit(api) {
  const arg = api && Array.isArray(api.args) ? api.args[1] : "";
  return arg === "poops" || arg === "relapse" ? arg : "";
}

function selectExploit(api, forced) {
  let fw = 0;
  let fwExact = "";

  const params = typeof location !== "undefined" && location.search
    ? new URLSearchParams(location.search) : null;
  const fwArg = params ? params.get("fw") : null;

  let match = typeof navigator !== "undefined" && navigator.userAgent
    ? /PlayStation 5\/(\d+\.\d+)/.exec(navigator.userAgent) : null;
  if (!match && fwArg) {
    match = ["PlayStation 5/" + fwArg, fwArg];
  }

  if (match) {
    fw = parseFloat(match[1]);
    fwExact = match[1];
  } else if (typeof api.fw === "string" && api.fw !== "unknown") {
    fw = parseFloat(api.fw);
    fwExact = api.fw;
  }

  const is905 = fwExact === "9.05" || Math.abs(fw - 9.05) < 0.001;
  const isOnline = typeof navigator !== "undefined" && !!navigator.onLine;
  const needsRelapse = fw > 12.00 && fw <= 13.60;

  if (forced === undefined) forced = forcedExploit(api);

  // Forced variant: honour it unless the firmware cannot run that exploit at
  // all, in which case fall through to the automatic rules below.
  if (forced === "poops" && !needsRelapse) {
    return Object.assign({ reason: "forced by Poops shortcut" }, POOPS);
  }
  if (forced === "relapse" && !is905) {
    return Object.assign({
      reason: "forced by Relapse shortcut",
      warning: isOnline ? null
        : "Relapse needs a connected network interface (LAN/Wi-Fi, no Internet required) and the browser reports offline.",
    }, RELAPSE);
  }
  const override = forced
    ? `forced ${forced} is not supported on FW ${fwExact || "?"}, falling back: `
    : "";

  if (needsRelapse) {
    return Object.assign({ reason: override + "FW > 12.00 requires Relapse" }, RELAPSE);
  }
  if (is905) {
    return Object.assign({ reason: override + "FW 9.05 requires Poops" }, POOPS);
  }
  if (isOnline) {
    return Object.assign({ reason: override + "network active" }, RELAPSE);
  }
  return Object.assign({ reason: override + "offline mode (FW <= 12.00)" }, POOPS);
}

async function fetchText(primary, fallback) {
  try {
    const res = await fetch(primary);
    if (res.ok) return await res.text();
  } catch (e) {}
  if (fallback) {
    const res2 = await fetch(fallback);
    if (res2.ok) return await res2.text();
  }
  throw new Error("Could not load " + primary);
}

return async function (api) {
  await api.log("[*] WebKit Autoloader initialized", "info");

  // Step 1: Check if ELF loader is already running
  const elfldrActive = await isElfldrRunning(api);
  if (elfldrActive) {
    await api.log("[+] ELF loader is already active on 127.0.0.1:9021. Skipping kernel exploit and autoload.", "success");
    return;
  }

  await api.log("[*] No active ELF loader on 9021.", "info");

  // Step 2: Check if already jailbroken
  const jb = await isJailbroken(api);
  if (jb) {
    await api.log("[+] Process is already jailbroken (uid 0). Skipping kernel exploit and autoload.", "success");
    return;
  }

  // Step 3: Determine which exploit to run
  const choice = selectExploit(api);
  await api.log(`[*] ${forcedExploit(api) ? "Forced" : "Auto"}-dispatch: selected ${choice.file} (${choice.reason})`, "info");
  if (choice.warning) {
    await api.log(`[!] ${choice.warning}`, "warn");
  }

  // Step 4: Pre-fetch all needed resources BEFORE kernel exploit runs
  const exploitCode = await fetchText(choice.file, choice.fallback);
  const autoloadCode = await fetchText("payloads/autoload.js", "autoload.js");

  const elfName = (api && Array.isArray(api.args) && api.args[0]) || "autoload.elf";
  await api.log(`[*] Pre-fetching ${elfName}...`, "info");
  let elfBytes = null;
  try {
    const elfResp = await fetch(elfName);
    if (elfResp.ok) {
      elfBytes = await elfResp.arrayBuffer();
    }
  } catch (e) {}

  // Step 5: Run the selected exploit
  const exploitArgs = choice.args || (choice.file.endsWith(".lua") ? ["compat=remote_lua_loader"] : []);
  await api.executePayload(exploitCode, exploitArgs);

  // Step 6: Transfer the autoloader ELF payload
  await api.log(`[*] Staging and sending ${elfName}...`, "info");
  await api.executePayload(autoloadCode, [elfName, elfBytes]);
};
