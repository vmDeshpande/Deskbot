#include "time_sync.h"

#include <time.h>
#include <esp_sntp.h>
#include "log.h"

namespace DeskbotTime {
namespace {

constexpr time_t kMinimumValidEpoch = 1700000000;
bool started = false;
bool synchronized = false;

}  // namespace

void begin() {
  // NTP starts only after station Wi-Fi is available; this call does not block.
  started = false;
  synchronized = false;
}

void loop(bool wifiConnected) {
  if (!wifiConnected) return;
  if (!started) {
    configTime(0, 0, "pool.ntp.org", "time.nist.gov", "time.google.com");
    started = true;
    LOG_INFO("TIME", "SNTP started; waiting for sync");
  }
  if (!synchronized && time(nullptr) >= kMinimumValidEpoch &&
      sntp_get_sync_status() == SNTP_SYNC_STATUS_COMPLETED) {
    synchronized = true;
    LOG_INFO("TIME", "UTC clock synchronized");
  }
}

bool isSynchronized() { return synchronized && time(nullptr) >= kMinimumValidEpoch; }

}  // namespace DeskbotTime