import { Navigation, QuickAccessTab } from "@decky/ui";

declare const SteamClient: any;

// Native toaster.toast({icon}) can render a broken image on some Steam builds.
// Same workaround as decky-proton-launch / decky-apps-manager: the notification
// passes for a chat message from one fixed synthetic friend whose avatar is the
// plugin icon.

const APP_NAME = "Decky NVIDIA Update";
const STEAMID_BASE = BigInt("76561197960265728");

const ICON_DATA_URL =
  "data:image/svg+xml;utf8," +
  encodeURIComponent(
    `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">
      <rect width="24" height="24" fill="#111"/>
      <g transform="translate(2.4 2.4) scale(0.8)">
        <path d="M8.948 8.798v-1.43a6.7 6.7 0 0 1 .424-.018c3.922-.124 6.493 3.374 6.493 3.374s-2.774 3.851-5.75 3.851c-.398 0-.787-.062-1.158-.185v-4.346c1.528.185 1.837.857 2.747 2.385l2.04-1.714s-1.492-1.952-4-1.952a6.016 6.016 0 0 0-.796.035m0-4.735v2.138l.424-.027c5.45-.185 9.01 4.47 9.01 4.47s-4.08 4.964-8.33 4.964c-.37 0-.733-.035-1.095-.097v1.325c.3.035.61.062.91.062 3.957 0 6.82-2.023 9.593-4.408.459.371 2.34 1.263 2.73 1.652-2.633 2.208-8.772 3.984-12.253 3.984-.335 0-.653-.018-.971-.053v1.864H24V4.063zm0 10.326v1.131c-3.657-.654-4.673-4.46-4.673-4.46s1.758-1.944 4.673-2.262v1.237H8.94c-1.528-.186-2.73 1.245-2.73 1.245s.68 2.412 2.739 3.11M2.456 10.9s2.164-3.197 6.5-3.533V6.201C4.153 6.59 0 10.653 0 10.653s2.35 6.802 8.948 7.42v-1.237c-4.84-.6-6.492-5.936-6.492-5.936z" fill="#76B900"/>
      </g>
    </svg>`,
  );

let h = 5381;
for (const c of APP_NAME) h = (Math.imul(h, 33) ^ c.charCodeAt(0)) >>> 0;
const ACCOUNT_ID = 0xde000000 + (h & 0xfffff);
const SID64 = (STEAMID_BASE + BigInt(ACCOUNT_ID)).toString();

function getFakePersona() {
  const fs = (window as any).friendStore;
  if (!fs?.GetFriendState) return null;
  return (
    fs.GetFriendState({
      GetAccountID: () => ACCOUNT_ID,
      ConvertTo64BitString: () => SID64,
      BIsValid: () => true,
    })?.m_persona ?? null
  );
}

function assertPersonaIdentity() {
  try {
    const p = getFakePersona();
    if (p && p.m_strPlayerName !== APP_NAME) p.m_strPlayerName = APP_NAME;
  } catch {}
}

let personaPrimed = false;
let personaGuard: ReturnType<typeof setInterval> | null = null;

function primePersona() {
  try {
    const p = getFakePersona();
    if (!p) return;
    try {
      p.m_strPlayerName = APP_NAME;
    } catch {}
    for (const k of [
      "avatar_url_small",
      "avatar_url_medium",
      "avatar_url_full",
    ]) {
      try {
        Object.defineProperty(p, k, {
          get: () => ICON_DATA_URL,
          configurable: true,
        });
      } catch {}
    }
    personaPrimed = true;
    // Steam refreshes unknown personas asynchronously and wipes the name.
    if (!personaGuard) personaGuard = setInterval(assertPersonaIdentity, 2000);
    for (const ms of [300, 800, 1500]) setTimeout(assertPersonaIdentity, ms);
  } catch {}
}

// The first notification of a Steam session can be posted before the toast
// window subscribes to it; resend once if nothing rendered.
const FIRST_TOAST_WAIT_MS = 900;
let firstToastChecked = false;

function verifyFirstToastRendered(resend: () => void) {
  if (firstToastChecked) return;
  firstToastChecked = true;
  const ns = (window as any).NotificationStore;
  if (typeof ns?.BAnyContextRenderingToasts !== "function") return;
  const start = Date.now();
  const iv = setInterval(() => {
    let rendered = false;
    try {
      rendered = !!ns.BAnyContextRenderingToasts();
    } catch {}
    if (rendered) {
      clearInterval(iv);
      return;
    }
    if (Date.now() - start >= FIRST_TOAST_WAIT_MS) {
      clearInterval(iv);
      try {
        resend();
      } catch {}
    }
  }, 60);
}

const openQuickAccessMenu = () =>
  Navigation.OpenQuickAccessMenu(QuickAccessTab.Decky);

export interface PluginToastData {
  title: string;
  body: string;
  onClick?: () => void;
}

export const pluginToast = (data: PluginToastData) => {
  try {
    primePersona();
    const message =
      data.title && data.title !== APP_NAME
        ? `${data.title}: ${data.body}`
        : data.body;
    const onClick = data.onClick ?? openQuickAccessMenu;
    const send = () =>
      SteamClient.ClientNotifications?.DisplayClientNotification?.(
        1,
        JSON.stringify({
          title: "",
          body: message,
          state: "active",
          steamid: SID64,
        }),
        () => {
          try {
            onClick();
          } catch {}
        },
      );
    send();
    if (!personaPrimed) verifyFirstToastRendered(send);
  } catch (e) {
    console.error("[nvidia-update] notification failed", e);
  }
};
