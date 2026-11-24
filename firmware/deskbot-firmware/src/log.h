#pragma once

#include <Arduino.h>

namespace DeskbotLog {

enum class Level : uint8_t {
    ERROR = 0,
    WARN  = 1,
    INFO  = 2,
    DEBUG = 3,
};

#ifndef DESKBOT_LOG_LEVEL
#define DESKBOT_LOG_LEVEL static_cast<uint8_t>(Level::INFO)
#endif

constexpr Level kLogLevel = static_cast<Level>(DESKBOT_LOG_LEVEL);

inline const char* levelName(Level level) {
    switch (level) {
        case Level::ERROR: return "ERROR";
        case Level::WARN:  return "WARN";
        case Level::INFO:  return "INFO";
        case Level::DEBUG: return "DEBUG";
    }
    return "UNKNOWN";
}

template<Level L, typename... Args>
void log(const char* tag, const char* format, Args... args) {
    if (L <= kLogLevel) {
        Serial.printf("[%s] [%s] ", levelName(L), tag);
        Serial.printf(format, args...);
        Serial.println();
    }
}

// Convenience macros
#define LOG_ERROR(tag, format, ...)  DeskbotLog::log<DeskbotLog::Level::ERROR>(tag, format, ##__VA_ARGS__)
#define LOG_WARN(tag, format, ...)   DeskbotLog::log<DeskbotLog::Level::WARN>(tag, format, ##__VA_ARGS__)
#define LOG_INFO(tag, format, ...)   DeskbotLog::log<DeskbotLog::Level::INFO>(tag, format, ##__VA_ARGS__)
#define LOG_DEBUG(tag, format, ...)  DeskbotLog::log<DeskbotLog::Level::DEBUG>(tag, format, ##__VA_ARGS__)

}  // namespace DeskbotLog