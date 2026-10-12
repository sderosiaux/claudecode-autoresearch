import {
  ArrowDownToLine,
  ArrowLeft,
  ArrowRight,
  Check,
  ChevronDown,
  ExternalLink,
  FlaskConical,
  Folder,
  PanelLeft,
  Pause,
  Play,
  Plus,
  RotateCw,
  Search,
  SlidersHorizontal,
  Square,
  X,
} from "lucide-react";
import type { CSSProperties } from "react";

const icons = {
  panel: PanelLeft,
  plus: Plus,
  search: Search,
  refresh: RotateCw,
  play: Play,
  pause: Pause,
  stop: Square,
  flask: FlaskConical,
  folder: Folder,
  settings: SlidersHorizontal,
  arrow: ArrowRight,
  back: ArrowLeft,
  download: ArrowDownToLine,
  external: ExternalLink,
  check: Check,
  close: X,
  chevron: ChevronDown,
} as const;

export default function Icon({
  name,
  size = 16,
  className,
  style,
}: {
  name: keyof typeof icons;
  size?: number;
  className?: string;
  style?: CSSProperties;
}) {
  const Component = icons[name];
  return (
    <Component
      size={size}
      strokeWidth={1.7}
      aria-hidden="true"
      className={className}
      style={style}
    />
  );
}
