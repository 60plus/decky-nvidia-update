import { useState } from "react";
import { Focusable, PanelSectionRow } from "@decky/ui";
import { ActionButton, InlineConfirm } from "@moi952/decky-ui-kit";
import { call } from "@decky/api";
import { useTranslation } from "react-i18next";
import { FaTrash } from "react-icons/fa";
import { CollapsibleSection } from "./CollapsibleSection";

export type CustomBuild = {
  version: string;
  built_at: string;
  packages: string[];
};

interface CustomBuildSectionProps {
  builds: CustomBuild[];
  onBuildsChange: () => void;
  expanded: boolean;
  onToggle: () => void;
  // True while a build or an install is running — deleting a cache entry
  // mid-run would be confusing at best.
  disabled?: boolean;
}

// Management for driver versions already built via steamos-nvidia-build.sh
// (see the version dropdown above, which is where building one is actually
// triggered — picking a version Arch hasn't packaged and hitting Install
// builds it first, automatically). This section is just "what's sitting in
// the cache, and a way to clear one out".
export function CustomBuildSection({
  builds,
  onBuildsChange,
  expanded,
  onToggle,
  disabled,
}: CustomBuildSectionProps) {
  const { t } = useTranslation("panel");
  const [confirmingDelete, setConfirmingDelete] = useState<string | null>(null);

  const onDelete = async (v: string) => {
    setConfirmingDelete(null);
    await call<[string], boolean>("delete_custom_build", v);
    onBuildsChange();
  };

  return (
    <CollapsibleSection
      label={t("custom_build_section_label")}
      expanded={expanded}
      onToggle={onToggle}
    >
      <PanelSectionRow>
        <div style={{ fontSize: 11, opacity: 0.75, marginBottom: 8 }}>
          {t("custom_build_description")}
        </div>
      </PanelSectionRow>

      {builds.length === 0 && (
        <PanelSectionRow>
          <div style={{ fontSize: 12, opacity: 0.7 }}>
            {t("custom_build_none_cached")}
          </div>
        </PanelSectionRow>
      )}
      {builds.map((b) =>
        confirmingDelete === b.version ? (
          <PanelSectionRow key={b.version}>
            <InlineConfirm
              description={t("custom_build_delete_confirm", {
                version: b.version,
              })}
              confirmLabel={t("custom_build_delete_confirm_button")}
              cancelLabel={t("action_cancel")}
              variant="danger"
              onCancel={() => setConfirmingDelete(null)}
              onConfirm={() => onDelete(b.version)}
            />
          </PanelSectionRow>
        ) : (
          <PanelSectionRow key={b.version}>
            <Focusable
              style={{
                display: "flex",
                alignItems: "center",
                justifyContent: "space-between",
                gap: 8,
                marginBottom: 6,
              }}
              flow-children="horizontal"
            >
              <span style={{ fontSize: 13 }}>{b.version}</span>
              <ActionButton
                onClick={() => setConfirmingDelete(b.version)}
                disabled={disabled}
              >
                <FaTrash size={12} />
              </ActionButton>
            </Focusable>
          </PanelSectionRow>
        ),
      )}
    </CollapsibleSection>
  );
}
