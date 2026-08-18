"use client";

import { useEffect } from "react";
import setupLocatorUI from "@locator/runtime";

export function LocatorRuntime() {
  /** 开发环境启用 LocatorJS，方便从页面元素反查源码位置。 */

  useEffect(() => {
    if (process.env.NODE_ENV === "development") {
      setupLocatorUI();
    }
  }, []);

  return null;
}
