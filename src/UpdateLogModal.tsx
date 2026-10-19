import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { call } from "@decky/api";
import { ConfirmModal, Focusable, GamepadButton, ScrollPanel } from "@decky/ui";
import { useTranslation } from "react-i18next";
import { withTimeout } from "./utils/driverOperation";

// Steam's scroll panel forwards Focusable props and registers its scroll region.
const LogScrollPanel = ScrollPanel as typeof Focusable;

type LogSnapshot = {
  lines: string[];
  truncated: boolean;
  path: string;
  error: string | null;
};

export function UpdateLogModal({ closeModal }: { closeModal?: () => void }) {
  const { t } = useTranslation("panel");
  const [log, setLog] = useState<LogSnapshot | null>(null);
  const [error, setError] = useState(false);
  const [following, setFollowing] = useState(true);
  const viewport = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let disposed = false;
    let timer: ReturnType<typeof setTimeout>;
    const refresh = async () => {
      try {
        const next = await withTimeout(call<[], LogSnapshot>("get_update_log"));
        if (disposed) return;
        setError(Boolean(next.error));
        if (!next.error) {
          setLog((previous) =>
            JSON.stringify(previous) === JSON.stringify(next) ? previous : next,
          );
        }
      } catch {
        if (!disposed) setError(true);
      } finally {
        if (!disposed) timer = setTimeout(refresh, 2000);
      }
    };
    void refresh();
    return () => {
      disposed = true;
      clearTimeout(timer);
    };
  }, []);

  useLayoutEffect(() => {
    if (following && viewport.current)
      viewport.current.scrollTop = viewport.current.scrollHeight;
  }, [log, following]);

  const move = (distance: number) => {
    if (!viewport.current) return;
    setFollowing(false);
    viewport.current.scrollTop += distance;
  };
  const latest = () => {
    setFollowing(true);
    if (viewport.current)
      viewport.current.scrollTop = viewport.current.scrollHeight;
  };

  return (
    <ConfirmModal
      strTitle={t("log_title")}
      strOKButtonText={t("close_button")}
      onOK={closeModal}
      onCancel={closeModal}
      bAlertDialog
      bAllowFullSize
      modalClassName="nvidia-update-log-modal"
    >
      <style>{`
        .nvidia-update-log-modal { width: min(900px, 90vw); max-width: 90vw; }
      `}</style>
      <div style={{ fontSize: 12, marginBottom: 8, opacity: 0.8 }}>
        {t("log_scroll_hint")} · {t(following ? "log_following" : "log_paused")}
      </div>
      {error && (
        <div role="status" style={{ color: "#ffcf5c", marginBottom: 8 }}>
          {t("log_unavailable")}
        </div>
      )}
      <LogScrollPanel
        ref={viewport}
        data-nvidia-update-log="true"
        role="region"
        aria-label={t("log_title")}
        tabIndex={0}
        onActivate={() => {}}
        onScroll={(event) => {
          const element = event.currentTarget;
          setFollowing(
            element.scrollHeight - element.clientHeight - element.scrollTop <
              24,
          );
        }}
        onKeyDown={(event) => {
          const height = event.currentTarget.clientHeight;
          const distances: Record<string, number> = {
            ArrowUp: -48,
            ArrowDown: 48,
            PageUp: -height * 0.8,
            PageDown: height * 0.8,
            Home: -event.currentTarget.scrollHeight,
          };
          if (event.key === "End") latest();
          else if (event.key in distances) move(distances[event.key]);
          else return;
          event.preventDefault();
          event.stopPropagation();
        }}
        onGamepadDirection={(event) => {
          if (event.detail.button === GamepadButton.DIR_UP) move(-64);
          else if (event.detail.button === GamepadButton.DIR_DOWN) move(64);
          else return;
          event.preventDefault();
          event.stopPropagation();
        }}
        style={{
          height: "min(50vh, 480px)",
          overflowY: "auto",
          overscrollBehavior: "contain",
          padding: "12px 16px",
          background: "rgba(0, 0, 0, 0.35)",
          borderRadius: 6,
          fontSize: 13,
          lineHeight: 1.5,
          fontFamily: "monospace",
          whiteSpace: "pre-wrap",
          overflowWrap: "anywhere",
          userSelect: "text",
        }}
      >
        {log
          ? log.lines.length > 0
            ? log.lines.join("\n")
            : t("log_empty")
          : t("log_loading")}
      </LogScrollPanel>
      {log?.truncated && (
        <div style={{ fontSize: 11, marginTop: 8 }}>
          {t("log_truncated", { count: log.lines.length })}
        </div>
      )}
      {log?.path && (
        <div
          style={{
            fontSize: 11,
            opacity: 0.6,
            marginTop: 8,
            overflowWrap: "anywhere",
          }}
        >
          {t("full_log_label", { path: log.path })}
        </div>
      )}
    </ConfirmModal>
  );
}
