# 0009 Hosting: portable Docker, provider decided later

**Decision.** Every service ships as a Docker image with a docker-compose file, so the stack runs on any
provider (VPS, Hetzner, DigitalOcean, AWS). The only hard requirement: static outbound IPs for broker order
traffic (SEBI). Provider choice is deferred to phase 15.
