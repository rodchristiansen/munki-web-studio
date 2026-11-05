// How Munki pkginfo fields are laid out in the editor. The only platform-specific
// part of the packages workspace; every key not listed here appears under Advanced.

export interface FieldSpec {
  key: string;
  label: string;
  kind?: "text" | "script" | "list";
}
export interface SectionSpec {
  title: string;
  fields: FieldSpec[];
}
export interface TabSpec {
  name: string;
  sections: SectionSpec[];
}

export const PLATFORM = {
  name: "Munki",
  descriptionLabel: "Shown in Managed Software Center",
};

export const TABS: TabSpec[] = [
  {
    name: "Overview",
    sections: [
      {
        title: "Identity",
        fields: [
          { key: "name", label: "Name" },
          { key: "display_name", label: "Display name" },
          { key: "version", label: "Version" },
          { key: "category", label: "Category" },
          { key: "developer", label: "Developer" },
        ],
      },
      { title: "Catalogs", fields: [{ key: "catalogs", label: "Catalogs", kind: "list" }] },
      { title: "Description", fields: [{ key: "description", label: PLATFORM.descriptionLabel, kind: "script" }] },
      {
        title: "Installer",
        fields: [
          { key: "installer_type", label: "Type" },
          { key: "installer_item_location", label: "Location" },
          { key: "installer_item_size", label: "Size (KB)" },
        ],
      },
      {
        title: "Requirements",
        fields: [
          { key: "minimum_os_version", label: "Minimum macOS" },
          { key: "maximum_os_version", label: "Maximum macOS" },
          { key: "supported_architectures", label: "Architectures", kind: "list" },
        ],
      },
    ],
  },
  {
    name: "Detection",
    sections: [
      { title: "Installs", fields: [{ key: "installs", label: "Items", kind: "list" }] },
      { title: "Receipts", fields: [{ key: "receipts", label: "Receipts", kind: "list" }] },
      {
        title: "Check scripts",
        fields: [
          { key: "installcheck_script", label: "Install check", kind: "script" },
          { key: "uninstallcheck_script", label: "Uninstall check", kind: "script" },
        ],
      },
    ],
  },
  {
    name: "Install",
    sections: [
      {
        title: "Behavior",
        fields: [
          { key: "unattended_install", label: "Unattended install" },
          { key: "blocking_applications", label: "Blocking applications", kind: "list" },
          { key: "items_to_copy", label: "Items to copy", kind: "list" },
          { key: "restart_action", label: "Restart action" },
        ],
      },
      {
        title: "Scripts",
        fields: [
          { key: "preinstall_script", label: "Preinstall", kind: "script" },
          { key: "postinstall_script", label: "Postinstall", kind: "script" },
        ],
      },
    ],
  },
  {
    name: "Uninstall",
    sections: [
      {
        title: "Removal",
        fields: [
          { key: "uninstallable", label: "Uninstallable" },
          { key: "uninstall_method", label: "Method" },
          { key: "unattended_uninstall", label: "Unattended uninstall" },
        ],
      },
      {
        title: "Scripts",
        fields: [
          { key: "preuninstall_script", label: "Preuninstall", kind: "script" },
          { key: "uninstall_script", label: "Uninstall", kind: "script" },
          { key: "postuninstall_script", label: "Postuninstall", kind: "script" },
        ],
      },
    ],
  },
  {
    name: "Advanced",
    sections: [
      {
        title: "Dependencies",
        fields: [
          { key: "requires", label: "Requires", kind: "list" },
          { key: "update_for", label: "Update for", kind: "list" },
        ],
      },
    ],
  },
];
