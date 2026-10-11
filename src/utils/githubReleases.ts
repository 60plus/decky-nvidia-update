// @ts-ignore — replaced at build time by rollup with the content of plugin.json
import manifest from "@decky/manifest";
import {
  fetchPluginReleases as fetchReleasesForRepo,
  type PluginRelease,
} from "@moi952/decky-plugin-toolkit";
import projectConfig from "../project.config.json";

export const CURRENT_VERSION: string = manifest?.version ?? "0.0.0";

const REPO = `${projectConfig.githubOwner}/${projectConfig.githubRepo}`;

export const fetchPluginReleases = (): Promise<PluginRelease[]> =>
  fetchReleasesForRepo(REPO);
