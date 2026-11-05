import icon from "../assets/munkistudio.png";

/** The studio's mark in the top bar. Platform-specific. */
export function Brand() {
  return <img className="mark" src={icon} alt="MunkiStudio app icon" width={36} height={36} />;
}
