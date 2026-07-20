var e = new Proxy({}, { get(e2, o) {
  throw new Error(`Module "module" has been externalized for browser compatibility. Cannot access "module.${o}" in client code.`);
} });
export {
  e as default
};
