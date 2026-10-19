import { findModuleExport, getFocusNavController } from "@decky/ui";
import { useLayoutEffect } from "react";

type Action = {
  label?: { props?: Record<string, unknown> };
  props?: Record<string, unknown>;
};
type Actions = Record<number, Action | undefined>;
type ActionStore = { SetActionsFromMap(map: Actions): void };
const nativeButtons = findModuleExport(
  (value) =>
    value &&
    typeof value === "object" &&
    "A" in value &&
    "RightStick" in value &&
    "RightStickClick" in value,
);

export const scrollHint = <span data-nvidia-scroll-hint>Scroll</span>;

// Like FileExplorer, keep Steam's native footer and controller glyph renderer.
// Its generic action converter maps unsupported stick actions to A. Correct
// only our marked hint, and restore the original store when this dialog closes.
export function useScrollHint() {
  useLayoutEffect(() => {
    const steam = (
      window as unknown as {
        SteamUIStore?: {
          GetFocusedWindowInstance(): { ActionDescriptionStore?: ActionStore };
        };
      }
    ).SteamUIStore;
    const store = steam?.GetFocusedWindowInstance()?.ActionDescriptionStore;
    if (!store || !nativeButtons) return;
    const original = store.SetActionsFromMap;
    const descriptor = Object.getOwnPropertyDescriptor(
      store,
      "SetActionsFromMap",
    );
    if (descriptor && !descriptor.configurable) return;
    const wrapper = (map: Actions) => {
      const hint = map[nativeButtons.A];
      if (
        (hint?.label?.props || hint?.props)?.["data-nvidia-scroll-hint"] ===
        undefined
      ) {
        original.call(store, map);
        return;
      }
      const nav = getFocusNavController() as unknown as {
        GetActiveNavTree(): {
          GetLastFocusedNode(): { GetActiveActionDescriptions(): Actions };
        };
      };
      const active = nav
        .GetActiveNavTree()
        ?.GetLastFocusedNode()
        ?.GetActiveActionDescriptions();
      if (!active) {
        original.call(store, map);
        return;
      }
      original.call(store, {
        ...map,
        [nativeButtons.A]: active[1],
        [nativeButtons.RightStick]: hint,
      });
    };
    Object.defineProperty(store, "SetActionsFromMap", {
      configurable: true,
      value: wrapper,
    });
    return () => {
      if (store.SetActionsFromMap !== wrapper) return;
      if (descriptor)
        Object.defineProperty(store, "SetActionsFromMap", descriptor);
      else delete (store as Partial<ActionStore>).SetActionsFromMap;
    };
  }, []);
}
