import { useEffect, useState } from 'react';

const defaults = { width: 336, height: 80 };

function readLayout(key: string) {
  try {
    const value = JSON.parse(localStorage.getItem(key) ?? 'null');
    return {
      width: Number.isFinite(value?.width)
        ? Math.max(280, Math.min(480, value.width))
        : defaults.width,
      height: Number.isFinite(value?.height)
        ? Math.max(80, Math.min(200, value.height))
        : defaults.height,
    };
  } catch {
    return defaults;
  }
}

export function useWorkspaceLayout(key: string) {
  const [sizes, setSizes] = useState(() => readLayout(key));
  useEffect(() => {
    try {
      localStorage.setItem(key, JSON.stringify(sizes));
    } catch {
      // Panel sizes remain usable when browser storage is unavailable.
    }
  }, [key, sizes]);
  return {
    ...sizes,
    setWidth: (width: number) => setSizes((value) => ({ ...value, width })),
    setHeight: (height: number) => setSizes((value) => ({ ...value, height })),
    resetWidth: () =>
      setSizes((value) => ({ ...value, width: defaults.width })),
    resetHeight: () =>
      setSizes((value) => ({ ...value, height: defaults.height })),
  };
}
