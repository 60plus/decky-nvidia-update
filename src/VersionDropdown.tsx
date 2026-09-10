import { useEffect, useState } from "react";
import { AnchoredDropdown } from "@moi952/decky-ui-kit";

export type RepoInfo = {
  stable: string;
  with_testing: string;
  testing_versions: string[];
};

export type NvidiaCategory = "recommended" | "nfb" | "beta";

export type NvidiaVersionInfo = {
  category: NvidiaCategory;
  date: string;
  notes: string[];
};

// Keyed by NVIDIA's bare version string (e.g. "610.57.04"), matching
// Arch's collapsed pkgver — covers as much of NVIDIA's own history as their
// endpoint returns, NOT just one "latest" pick per category. A version
// Arch has that isn't a key here is simply unclassified.
export type NvidiaVersionMap = Record<string, NvidiaVersionInfo>;

export type ChannelFilters = {
  recommended: boolean;
  nfb: boolean;
  beta: boolean;
  // Whether NVIDIA-published versions Arch hasn't packaged (buildable via
  // steamos-nvidia-build.sh, not yet in the custom-build cache) show up in
  // the list at all — this bucket can otherwise get long.
  tocompile: boolean;
};

// "all" and "tocompile" are only ever valid values for the default-channel
// SETTING, never an actual per-version classification (NVIDIA's own lookup
// never tags a version either of those — "tocompile" is a build-status
// concept, not a quality tier).
export type DefaultChannelChoice = NvidiaCategory | "all" | "tocompile";

// Numeric, component-by-component comparison ("615.71.09-1" vs
// "610.57.04-1"), not string equality — a custom-built version is very
// often numerically AHEAD of anything Arch currently packages, which a
// plain `!==`/`===` check would get wrong. Splits on any non-digit run, so
// it doesn't care whether it's fed a bare pkgver or a pkgver-pkgrel string;
// a missing trailing component compares as 0 (so "615.71.9" and
// "615.71.9.0" are equal, matching normal version semantics). Exported —
// index.tsx's own up-to-date/update-available check uses this exact rule.
export function compareDriverVersions(a: string, b: string): number {
  const aParts = a.split(/\D+/).filter(Boolean).map(Number);
  const bParts = b.split(/\D+/).filter(Boolean).map(Number);
  const len = Math.max(aParts.length, bParts.length);
  for (let i = 0; i < len; i++) {
    const diff = (aParts[i] ?? 0) - (bParts[i] ?? 0);
    if (diff !== 0) return diff;
  }
  return 0;
}

interface VersionDropdownProps {
  versions: string[];
  detailedVersions: string[];
  showBuildNumbers: boolean;
  repoInfo: RepoInfo;
  latestVersion: string;
  nvidiaVersions: NvidiaVersionMap;
  channelFilters: ChannelFilters;
  // Which category the dropdown defaults to when nothing's been picked yet
  // (user-configurable in Content, no longer hardcoded to "recommended").
  // "all" means "whichever is newest, regardless of category". "tocompile"
  // means "whichever NVIDIA version is newest, period — built or not".
  defaultChannel: DefaultChannelChoice;
  // Localized labels — this component stays i18n-free itself (no
  // react-i18next dependency), so Content passes already-translated text.
  categoryLabels: Record<NvidiaCategory, string>;
  archTestingLabel: string;
  // Versions built locally via steamos-nvidia-build.sh (see
  // CustomBuildSection) — Arch never packaged these, so they're not in
  // `versions`/`detailedVersions` at all. Always shown, above everything
  // else, regardless of channel filters — these are already sitting in the
  // cache, ready to install immediately.
  customBuildVersions?: string[];
  customBuildLabel: string;
  // Every NVIDIA-published version (see list_buildable_versions), newest
  // first — used both to compute the "tocompile" default (the single
  // newest NVIDIA release, built or not) and to derive which ones are
  // neither Arch-provided nor already cached (shown tagged with
  // needsBuildLabel, subject to channelFilters.tocompile).
  buildableVersions?: string[];
  needsBuildLabel: string;
  // What's actually installed right now — used only to decide the DEFAULT
  // selection (see defaultVersion below), never to filter/hide anything.
  currentVersion: string;
  disabled?: boolean;
  onSelectionChange: (version: string) => void;
}

// Confirmed via instrumentation: Steam's own native dropdown (DialogDropdown,
// what @decky/ui's DropdownItem renders) tears down and recreates the ENTIRE
// plugin panel the instant an option is picked from it. AnchoredDropdown
// (@moi952/decky-ui-kit) renders its own inline overlay instead of that
// native popup specifically to avoid this — switching to it is the real
// fix, but this restore-window trick is left in place as a harmless safety
// net in case some other remount trigger is ever found.
const RESTORE_WINDOW_MS = 5000;
let lastSelected = "";
let lastSelectedAt = 0;

export function VersionDropdown({
  versions,
  detailedVersions,
  showBuildNumbers,
  repoInfo,
  latestVersion,
  nvidiaVersions,
  channelFilters,
  defaultChannel,
  categoryLabels,
  archTestingLabel,
  customBuildVersions,
  customBuildLabel,
  buildableVersions,
  needsBuildLabel,
  currentVersion,
  disabled,
  onSelectionChange,
}: VersionDropdownProps) {
  const displayVersions = showBuildNumbers ? detailedVersions : versions;
  const testingPkgvers = repoInfo.testing_versions.map((v) =>
    v.replace(/-\d+$/, ""),
  );

  useEffect(() => {
    console.log(
      `[nvidia-update] Arch versions: ${versions.length}, detailed: ${detailedVersions.length}, ` +
        `NVIDIA classified: ${Object.keys(nvidiaVersions).length}`,
      nvidiaVersions,
    );
  }, [versions, detailedVersions, nvidiaVersions]);

  // Every Arch-available version is kept UNLESS it's classified into a
  // category whose checkbox is currently off. A version NVIDIA doesn't
  // classify at all is always shown — there's no basis to hide it.
  const archOptions = displayVersions
    .map((v) => {
      const bareVersion = v.replace(/-\d+$/, "");
      const info = nvidiaVersions[bareVersion];
      if (info && !channelFilters[info.category]) return null;
      const isArchTesting = showBuildNumbers
        ? repoInfo.testing_versions.includes(v)
        : testingPkgvers.includes(v);
      const label = info
        ? `${v} (${categoryLabels[info.category]})`
        : isArchTesting
          ? `${v} (${archTestingLabel})`
          : v;
      return { data: v, label };
    })
    .filter((o): o is { data: string; label: string } => o !== null);

  // Custom-built versions never appear in `displayVersions` (Arch has no
  // idea they exist) — shown above everything else.
  const customOptions = (customBuildVersions ?? []).map((v) => ({
    data: v,
    label: `${v} (${customBuildLabel})`,
  }));

  // Everything NVIDIA has published that's neither already Arch-provided
  // nor already sitting in the custom-build cache — offered inline, tagged
  // as needing a build first, rather than only reachable from a separate
  // section. Hidden entirely when the tocompile filter is off.
  const archBareVersions = new Set(
    displayVersions.map((v) => v.replace(/-\d+$/, "")),
  );
  const customBareVersions = new Set(customBuildVersions ?? []);
  const toCompileOptions = channelFilters.tocompile
    ? (buildableVersions ?? [])
        .filter((v) => !archBareVersions.has(v) && !customBareVersions.has(v))
        .map((v) => ({ data: v, label: `${v} (${needsBuildLabel})` }))
    : [];

  // A single list sorted by actual release, newest first — NOT the three
  // source buckets simply concatenated (custom-built versions are usually
  // recent, but the to-compile bucket spans NVIDIA's whole history back to
  // 590, so appending it wholesale after customOptions produced a visibly
  // non-chronological order, e.g. a 590.x "to compile" entry sitting above
  // a 610.x Arch-provided one).
  const options = [...customOptions, ...toCompileOptions, ...archOptions].sort(
    (a, b) => compareDriverVersions(b.data, a.data),
  );

  const activeVersions = options.map((o) => o.data);

  // Prefer defaulting to whatever is tagged as the user's chosen default
  // channel in the still-visible list; otherwise fall back to the plain
  // latest (stable) pick. "all" skips the category match entirely and just
  // takes the first (newest) Arch option, since the lists are already
  // newest-first. "tocompile" instead means "the single newest version
  // NVIDIA has ever published" — buildableVersions already contains every
  // release NVIDIA has, built or not, newest first, so its own head *is*
  // that answer directly (no need to merge customOptions/toCompileOptions
  // to find it).
  const preferredOption =
    defaultChannel === "all" || defaultChannel === "tocompile"
      ? undefined
      : archOptions.find((o) => {
          const info = nvidiaVersions[o.data.replace(/-\d+$/, "")];
          return info?.category === defaultChannel;
        });
  const fallbackDefault = showBuildNumbers
    ? latestVersion
    : latestVersion.replace(/-\d+$/, "");
  const bestArchPick =
    defaultChannel === "tocompile"
      ? (buildableVersions?.[0] ?? fallbackDefault)
      : (preferredOption?.data ?? archOptions[0]?.data ?? fallbackDefault);

  // What's actually installed beats "the newest for this channel" whenever
  // it's at least as new (numerically) — e.g. a custom-built version ahead
  // of anything Arch currently packages. Only applies while that installed
  // version is still a selectable option at all (it may have been removed
  // from the custom-build cache since being installed); otherwise this
  // falls through to the plain "newest for this channel" pick, same as
  // before currentVersion existed as a factor here.
  //
  // currentVersion always comes from `pacman -Q` as "pkgver-pkgrel" (e.g.
  // "615.71.09-1"), but the option that actually represents it in this
  // list may be bare (the collapsed Arch list, and every custom-build
  // entry, are always bare pkgvers with no pkgrel) — an exact-string
  // membership check would silently never match in that case. Try an exact
  // match first (covers the detailed/showBuildNumbers list, which does
  // carry pkgrel), then fall back to matching on the bare pkgver.
  const currentVersionBare = currentVersion.replace(/-\d+$/, "");
  const currentVersionAsOption =
    (activeVersions.includes(currentVersion) ? currentVersion : undefined) ??
    activeVersions.find((v) => v.replace(/-\d+$/, "") === currentVersionBare);
  const defaultVersion =
    currentVersionAsOption !== undefined &&
    compareDriverVersions(currentVersion, bestArchPick) >= 0
      ? currentVersionAsOption
      : bestArchPick;

  const [selected, setSelected] = useState<string>(() =>
    Date.now() - lastSelectedAt < RESTORE_WINDOW_MS ? lastSelected : "",
  );
  const effectiveSelected =
    selected !== "" && activeVersions.includes(selected)
      ? selected
      : defaultVersion;

  // Syncs whatever is actually showing (persisted pick, or the default while
  // none has been made yet) up to the parent — fires on mount and whenever
  // it changes, so a remount immediately re-reports the persisted pick
  // instead of silently reverting the parent's chosenVersion to "".
  useEffect(() => {
    onSelectionChange(effectiveSelected);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [effectiveSelected]);

  return (
    // AnchoredDropdown (0.6.0) has no `disabled` prop of its own — this
    // wrapper is the substitute: dims it and blocks interaction the same
    // way a real disabled control would.
    <div style={disabled ? { opacity: 0.5, pointerEvents: "none" } : undefined}>
      <AnchoredDropdown
        options={options.map((o) => ({ value: o.data, label: o.label }))}
        selectedValue={effectiveSelected}
        onChange={(version) => {
          lastSelected = version;
          lastSelectedAt = Date.now();
          setSelected(version);
          onSelectionChange(version);
        }}
      />
    </div>
  );
}
