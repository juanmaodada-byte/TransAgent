import type { ButtonHTMLAttributes, ReactNode } from 'react';
import './ui.css';

type ButtonVariant = 'default' | 'secondary' | 'outline' | 'ghost' | 'destructive' | 'link';
type ButtonSize = 'sm' | 'md' | 'lg' | 'icon';

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  size?: ButtonSize;
  icon?: ReactNode;
  trailingIcon?: ReactNode;
}

function cx(...classes: Array<string | false | null | undefined>) {
  return classes.filter(Boolean).join(' ');
}

export function Button({
  className,
  variant = 'default',
  size = 'md',
  icon,
  trailingIcon,
  children,
  type = 'button',
  ...props
}: ButtonProps) {
  return (
    <button
      className={cx('ui-button', `ui-button-${variant}`, `ui-button-${size}`, className)}
      type={type}
      {...props}
    >
      {icon && <span className="ui-button-icon-wrap">{icon}</span>}
      {children}
      {trailingIcon && <span className="ui-button-icon-wrap">{trailingIcon}</span>}
    </button>
  );
}
