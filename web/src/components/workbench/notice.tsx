import { AlertCircle, AlertTriangle, CheckCircle2, Info } from 'lucide-react';
import type { ReactNode } from 'react';

const icons = {
  info: Info,
  success: CheckCircle2,
  warning: AlertTriangle,
  error: AlertCircle,
};

export function WorkbenchNotice({
  intent = 'info',
  title,
  children,
}: {
  intent?: keyof typeof icons;
  title: string;
  children?: ReactNode;
}) {
  const Icon = icons[intent];
  return (
    <div
      className="rf-notice"
      data-intent={intent}
      role={intent === 'error' ? 'alert' : 'status'}
    >
      <Icon className="size-4 shrink-0 mt-0.5" aria-hidden />
      <div className="min-w-0">
        <p className="font-medium">{title}</p>
        {children && <div className="mt-1 text-sm">{children}</div>}
      </div>
    </div>
  );
}
