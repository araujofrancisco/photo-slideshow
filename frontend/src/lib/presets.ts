export interface ResolutionPreset {
  label: string;
  value: string;
  width: number;
  height: number;
}

export const RESOLUTION_PRESETS: ResolutionPreset[] = [
  { label: "HD (720p)", value: "1280x720", width: 1280, height: 720 },
  { label: "Full HD (1080p)", value: "1920x1080", width: 1920, height: 1080 },
  { label: "2K (1440p)", value: "2560x1440", width: 2560, height: 1440 },
  { label: "4K (2160p)", value: "3840x2160", width: 3840, height: 2160 },
  { label: "Instagram Square", value: "1080x1080", width: 1080, height: 1080 },
  { label: "Instagram Story", value: "1080x1920", width: 1080, height: 1920 },
];

export const CUSTOM_PRESET_VALUE = "custom";

export function estimateRenderTime(
  imageCount: number,
  delaySeconds: number,
  transition: string,
  crossfadeSeconds: number,
): number {
  const totalDuration =
    imageCount * delaySeconds -
    (transition === "crossfade" ? (imageCount - 1) * crossfadeSeconds : 0);
  return Math.max(5, totalDuration * 0.3);
}
