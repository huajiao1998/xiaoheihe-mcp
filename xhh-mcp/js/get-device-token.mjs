// 只做一件事：跑官方 fp.min.js，拿设备令牌
import { JSDOM, ResourceLoader, VirtualConsole } from "jsdom";
import crypto from "node:crypto";

const SDK_URL = 'https://static.portal101.cn/dist/web/v3.0.0/fp.min.js';
const SDK_SHA256 = '7de91adbb7666216d9a72ba1c8655003e393aa5235f6345e399425cd78469a78';
const PATTERN = /^B[A-Za-z0-9+/=_-]{88}$/;
const CONF = {
  organization: '0yD85BjYvGFAvHaSQ1mc', appId: 'heybox_website',
  publicKey: 'MIGfMA0GCSqGSIb3DQEBAQUAA4GNADCBiQKBgQCXj9exmI4nQjmT52iwr+yf7hAQ06bfSZHTAHUfRBYiagCf/whhd8es0R79wBigpiHLd28TKA8b8mGR8OiiI1hV+qfynCWihvp3mdj8MiiH6SU3lhro2hkfYzImZB0RmWr2zE4Xt1+A6Oyp6bf+W7JSxYUXHw3nNv7Td4jw4jEFKQIDAQAB',
  staticHost: 'static.portal101.cn', protocol: 'https', noNeedRetry: true,
};

const FP = {
  ua: 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
  nav: { appCodeName: 'Mozilla', appName: 'Netscape', appVersion: '5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
         language: 'zh-CN', languages: ['zh-CN', 'zh'], platform: 'Linux x86_64',
         hardwareConcurrency: 4, deviceMemory: 8, maxTouchPoints: 0,
         vendor: 'Google Inc.', product: 'Gecko', productSub: '20030107' },
  screen: { width: 1920, height: 1080, availWidth: 1920, availHeight: 1053, colorDepth: 24, pixelDepth: 24, availTop: 0, availLeft: 0 },
  win: { innerWidth: 1920, innerHeight: 947, outerWidth: 1920, outerHeight: 1040, devicePixelRatio: 1 },
};

function setProps(target, values, keys) {
  for (const k of keys) if (values[k] !== undefined)
    Object.defineProperty(target, k, { configurable: true, get: () => values[k] });
}

async function main() {
  const src = await (await fetch(SDK_URL)).text();
  if (crypto.createHash('sha256').update(src).digest('hex') !== SDK_SHA256) {
    console.error('SDK 校验失败（官网可能更新了）'); process.exit(2);
  }
  const dom = new JSDOM('<!doctype html><html><head></head><body></body></html>', {
    url: 'https://www.xiaoheihe.cn/', runScripts: 'outside-only',
    resources: new ResourceLoader({ userAgent: FP.ua }), virtualConsole: new VirtualConsole(),
  });
  const w = dom.window;
  setProps(w.navigator, FP.nav, Object.keys(FP.nav));
  setProps(w.screen, FP.screen, Object.keys(FP.screen));
  setProps(w, FP.win, Object.keys(FP.win));
  w.SMSdk = {};
  const token = await new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error('超时')), 30000);
    w._smConf = { ...CONF, onError: v => { clearTimeout(timer); reject(new Error('SDK 报错 ' + v)); } };
    const poll = setInterval(() => {
      try {
        const t = w.SMSdk.getDeviceId && w.SMSdk.getDeviceId();
        if (typeof t === 'string' && PATTERN.test(t)) { clearTimeout(timer); clearInterval(poll); resolve(t); }
      } catch (e) { clearTimeout(timer); clearInterval(poll); reject(e); }
    }, 200);
    w.eval(src);
  });
  console.log(JSON.stringify({ token, length: token.length }));
}
main().catch(e => { console.error('失败:', e.message); process.exit(1); });
