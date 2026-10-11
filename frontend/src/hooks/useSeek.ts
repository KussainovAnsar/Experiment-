/**
 * Seek context: lets any timecode inside the lesson page seek the video and highlight the nearest frame.
 * Outside of a provider the seek is a no-op.
 */
import { createContext, useContext } from "react";

export type SeekFn = (seconds: number) => void;

export interface SeekContextValue {
  seek: SeekFn;
  /** Currently highlighted position (seconds), null when nothing selected. */
  position: number | null;
}

export const SeekContext = createContext<SeekContextValue>({ seek: () => undefined, position: null });

export function useSeek(): SeekFn {
  return useContext(SeekContext).seek;
}

export function useSeekPosition(): number | null {
  return useContext(SeekContext).position;
}
