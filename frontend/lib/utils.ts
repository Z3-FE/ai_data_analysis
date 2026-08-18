import { clsx, type ClassValue } from "clsx"
import { twMerge } from "tailwind-merge"

export function cn(...inputs: ClassValue[]) {
  /** 合并 Tailwind className，解决条件类名和冲突类名的问题。 */

  return twMerge(clsx(inputs))
}
