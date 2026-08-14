import http from "k6/http";
import { check, sleep } from "k6";

export const options = {
  insecureSkipTLSVerify: true,
  vus: 5,
  duration: "15s",
  thresholds: {
    http_req_failed: ["rate<0.01"],
    http_req_duration: ["p(95)<750"],
    checks: ["rate>0.99"],
  },
};

const baseUrl = __ENV.BASE_URL || "https://127.0.0.1:18443";

export default function () {
  for (const path of ["/", "/api/health/live", "/api/health/ready"]) {
    const response = http.get(`${baseUrl}${path}`);
    check(response, { [`${path} returns 200`]: (item) => item.status === 200 });
  }
  sleep(0.1);
}
