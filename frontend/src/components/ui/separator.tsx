import type { HTMLAttributes } from 'react';
import './ui.css';

function cx(...classes: Array<string | false | null | undefined>) {
  return classes.filter(Boolean).join(' ');
}

export function Separator({ className, ...props }: HTMLAttributes<HTMLDivElement>) {
  return <div className={cx('ui-separator', className)} {...props} />;
}
