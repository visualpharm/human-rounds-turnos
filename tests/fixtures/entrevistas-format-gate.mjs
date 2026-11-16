export function makeFormatGate() {
  let resolve;
  let reject;
  const promise = new Promise((res, rej) => {
    resolve = res;
    reject = rej;
  });
  promise.catch(() => {});
  return {
    promise,
    release() {
      resolve();
    },
    fail(error) {
      reject(error || new Error("synthetic format load failure"));
    },
  };
}
