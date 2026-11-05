export type IconName = "package" | "manifest" | "branch" | "search" | "review" | "grid" | "folder" | "activity";

const PATHS: Record<IconName, React.ReactNode> = {
  package: <path d="m12 3 9 5-9 5-9-5 9-5Zm-9 5v10l9 5 9-5V8M12 13v10M7.5 5.5l9 5" />,
  manifest: (
    <>
      <rect x="3" y="4" width="18" height="16" rx="2" />
      <path d="M7 8h2m3 0h5M7 12h2m3 0h5M7 16h2m3 0h5" />
    </>
  ),
  branch: (
    <>
      <circle cx="6" cy="5" r="2.5" />
      <circle cx="6" cy="19" r="2.5" />
      <circle cx="18" cy="5" r="2.5" />
      <path d="M6 7.5v9m12-9c0 6-12 3-12 9" />
    </>
  ),
  search: (
    <>
      <circle cx="10" cy="10" r="6.5" />
      <path d="m15 15 5 5" />
    </>
  ),
  activity: <path d="M3 12h4l3-8 4 16 3-8h4" />,
  review: <path d="M12 3v12m-4-4 4 4 4-4M4 13v7h16v-7" />,
  grid: (
    <>
      <rect x="3" y="3" width="6" height="6" rx="1" />
      <rect x="15" y="3" width="6" height="6" rx="1" />
      <rect x="3" y="15" width="6" height="6" rx="1" />
      <rect x="15" y="15" width="6" height="6" rx="1" />
    </>
  ),
  folder: <path d="M3 7V5h7l2 2h9v13H3V7Z" />,
};

export function Icon({ name }: { name: IconName }) {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      {PATHS[name]}
    </svg>
  );
}
