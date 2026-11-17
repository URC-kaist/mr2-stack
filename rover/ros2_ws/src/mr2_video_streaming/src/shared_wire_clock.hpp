#pragma once
#include <pthread.h>
#include <sys/mman.h>
#include <sys/syscall.h>
#include <unistd.h>
#include <fcntl.h>
#include <algorithm>
#include <cstdint>
#include <cerrno>

// Payload stays in each camera's process. Only packet scheduling is shared.
// Each camera can reserve at most its next packet, bounding aggregate lookahead.
class SharedWireClock
{
  struct State { pthread_mutex_t mutex; int kbps; uint64_t next_us; };
  int fd_{-1};
  State * state_{nullptr};
  bool lock() const
  {
    const int r = pthread_mutex_lock(&state_->mutex);
    if (r == EOWNERDEAD) {pthread_mutex_consistent(&state_->mutex); return true;}
    return r == 0;
  }

public:
  SharedWireClock() = default;
  SharedWireClock(const SharedWireClock &) = delete;
  ~SharedWireClock() {if (state_) {munmap(state_, sizeof(State));} if (fd_ >= 0) {close(fd_);}}
  bool create()
  {
    const int fd = int(syscall(SYS_memfd_create, "mr2-video-pacing", 1U));
    if (fd < 0) {return false;}
    if (ftruncate(fd, sizeof(State)) != 0 || !attach(fd)) {close(fd); return false;}
    close(fd);
    pthread_mutexattr_t attr;
    pthread_mutexattr_init(&attr);
    pthread_mutexattr_setpshared(&attr, PTHREAD_PROCESS_SHARED);
    pthread_mutexattr_setrobust(&attr, PTHREAD_MUTEX_ROBUST);
    const int result = pthread_mutex_init(&state_->mutex, &attr);
    pthread_mutexattr_destroy(&attr);
    return result == 0;
  }
  bool attach(int fd)
  {
    if (state_) {return true;}
    const int copy = fcntl(fd, F_DUPFD_CLOEXEC, 3);
    if (copy < 0) {return false;}
    void * p = mmap(nullptr, sizeof(State), PROT_READ | PROT_WRITE, MAP_SHARED, copy, 0);
    if (p == MAP_FAILED) {close(copy); return false;}
    fd_ = copy; state_ = static_cast<State *>(p); return true;
  }
  int fd() const {return fd_;}
  bool available() const {return state_ != nullptr;}
  void set_rate(int rate)
  {
    if (!state_ || !lock()) {return;}
    state_->kbps = std::max(0, rate);
    if (rate <= 0) {state_->next_us = 0;}
    pthread_mutex_unlock(&state_->mutex);
  }
  int rate() const
  {
    if (!state_ || !lock()) {return 0;}
    const int rate = state_->kbps; pthread_mutex_unlock(&state_->mutex); return rate;
  }
  uint64_t backlog(uint64_t now) const
  {
    if (!state_ || !lock()) {return 0;}
    const auto delay = state_->next_us > now ? state_->next_us - now : 0;
    pthread_mutex_unlock(&state_->mutex); return delay;
  }
  uint64_t reserve(uint64_t bytes, uint64_t earliest)
  {
    if (!state_ || !lock()) {return earliest;}
    if (state_->kbps <= 0) {pthread_mutex_unlock(&state_->mutex); return UINT64_MAX;}
    const auto at = std::max(earliest, state_->next_us);
    state_->next_us = at + bytes * 8000 / std::max(1, state_->kbps);
    pthread_mutex_unlock(&state_->mutex); return at;
  }
};
