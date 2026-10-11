// @ts-ignore — replaced at build time by rollup with the content of plugin.json
import manifest from "@decky/manifest";
import type { PluginRelease } from "@moi952/decky-plugin-toolkit";

export const CURRENT_VERSION: string = manifest?.version ?? "0.0.0";

// Local standalone builds must not offer the upstream in-place installer.
export const fetchPluginReleases = (): Promise<PluginRelease[]> =>
  Promise.resolve([]);
