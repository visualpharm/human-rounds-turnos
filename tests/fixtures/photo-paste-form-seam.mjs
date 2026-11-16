// Test seam for the account dashboard's photo surfaces: the REAL photo
// helpers from platform/account-form.mjs (photoFileFromEvent,
// eventCarriesFiles, isHeicFile, fileAsDataUrl — the behavior under test)
// with a CONTROLLABLE fileToJpeg (canvas re-encode: node has no canvas).
// Tests set globalThis.__photoPasteFileToJpeg to force success or failure.
import * as real from "../../static/platform/account-form.mjs";

export const renderEditar = () => {};
export const renderAuth = () => {};
export const photoFileFromEvent = real.photoFileFromEvent;
export const eventCarriesFiles = real.eventCarriesFiles;
export const isHeicFile = real.isHeicFile;
export const fileAsDataUrl = real.fileAsDataUrl;
export function fileToJpeg(file, maxDim) {
  const hook = globalThis.__photoPasteFileToJpeg;
  if (hook) return hook(file, maxDim);
  return Promise.resolve("data:image/jpeg;base64,VEVTVC1KUEVH");
}
