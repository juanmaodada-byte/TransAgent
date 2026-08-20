import type { LucideIcon } from 'lucide-react';
import {
  AlertTriangle,
  ArrowLeft,
  ArrowRight,
  BookMarked,
  BookOpen,
  Bot,
  ChevronDown,
  ClipboardList,
  Check,
  CheckCircle2,
  Circle,
  CircleSlash,
  Clock3,
  Compass,
  Copy,
  Download,
  Eye,
  EyeOff,
  File,
  FileCode2,
  FileDown,
  FileSearch,
  FileText,
  FileType,
  FolderOpen,
  FolderPlus,
  Globe2,
  HelpCircle,
  Image,
  KeyRound,
  Languages,
  Loader2,
  Save,
  MapPin,
  PackageOpen,
  PanelRightOpen,
  Paperclip,
  Pencil,
  Plus,
  Repeat2,
  Rocket,
  RotateCcw,
  SendHorizontal,
  Settings,
  ShieldCheck,
  SkipForward,
  Sparkles,
  Square,
  Upload,
  UserRoundCheck,
  X,
  XCircle,
  Activity,
} from 'lucide-react';
import type { FormatType, StepState } from '../../types';

export function Icon({
  icon: IconComponent,
  className,
  size = 16,
}: {
  icon: LucideIcon;
  className?: string;
  size?: number;
}) {
  return (
    <IconComponent
      className={className}
      size={size}
      strokeWidth={2}
      aria-hidden="true"
    />
  );
}

const formatIconMap: Record<FormatType, LucideIcon> = {
  md: FileCode2,
  docx: FileType,
  doc: FileText,
  pdf: FileText,
  text: File,
  image: Image,
};

export function FormatIcon({ format, className }: { format: FormatType; className?: string }) {
  return <Icon icon={formatIconMap[format] ?? FileText} className={className} size={18} />;
}

export function SourceIcon({ source, className }: { source: string; className?: string }) {
  const iconMap: Record<string, LucideIcon> = {
    RAG命中: BookMarked,
    Web搜索: Globe2,
    LLM生成: Bot,
    用户确认: UserRoundCheck,
    白名单: ShieldCheck,
  };
  return <Icon icon={iconMap[source] ?? HelpCircle} className={className} size={13} />;
}

export function StepStateIcon({
  state,
  className,
}: {
  state: StepState;
  className?: string;
}) {
  const iconMap: Record<StepState, LucideIcon> = {
    pending: Circle,
    in_progress: Loader2,
    completed: CheckCircle2,
    failed: XCircle,
    skipped: CircleSlash,
    waiting_user: Clock3,
  };
  return <Icon icon={iconMap[state]} className={className} size={14} />;
}

export {
  AlertTriangle,
  Activity,
  ArrowLeft,
  ArrowRight,
  BookOpen,
  Bot,
  ClipboardList,
  Check,
  CheckCircle2,
  ChevronDown,
  Clock3,
  Compass,
  Copy,
  Download,
  Eye,
  EyeOff,
  File,
  FileCode2,
  FileText,
  FileDown,
  FileSearch,
  FileType,
  FolderOpen,
  FolderPlus,
  Globe2,
  Image,
  KeyRound,
  Languages,
  Loader2,
  Save,
  MapPin,
  PackageOpen,
  PanelRightOpen,
  Paperclip,
  Pencil,
  Plus,
  Repeat2,
  Rocket,
  RotateCcw,
  Settings,
  SkipForward,
  Sparkles,
  SendHorizontal,
  Square,
  Upload,
  X,
};
