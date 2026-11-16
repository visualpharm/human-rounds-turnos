let resolve;
let reject;
export const accountFormGate = {
  promise: new Promise((res, rej) => {
    resolve = res;
    reject = rej;
  }),
  release() {
    resolve();
  },
  fail(error) {
    reject(error || new Error("synthetic account-form load failure"));
  },
};
// fail() may run before the gated module is awaiting. Keep that rejection
// handled so Node does not flag it as activity after the test ended.
accountFormGate.promise.catch(() => {});
