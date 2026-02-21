# Speedometer Real-Time Application Makefile

CXX = g++
CXXFLAGS = -std=c++17 -Wall -Wextra -O2 -pthread
LDFLAGS = -lrt -lpthread

TARGETS = speedometer cpu_workload memory_workload io_workload misbehaving_app

.PHONY: all clean install

all: $(TARGETS)

speedometer: speedometer.cpp
	$(CXX) $(CXXFLAGS) -o $@ $< $(LDFLAGS)

cpu_workload: cpu_workload.cpp
	$(CXX) $(CXXFLAGS) -o $@ $< -lpthread

memory_workload: memory_workload.cpp
	$(CXX) $(CXXFLAGS) -o $@ $< -lpthread

io_workload: io_workload.cpp
	$(CXX) $(CXXFLAGS) -o $@ $< -lpthread

misbehaving_app: misbehaving_app.cpp
	$(CXX) $(CXXFLAGS) -o $@ $< -lpthread

clean:
	rm -f $(TARGETS) *.o *.csv *.log *.dat *.tmp misbehaving_io_*.tmp io_workload_temp.dat

install: all
	install -m 755 $(TARGETS) /usr/local/bin/

help:
	@echo "Speedometer Real-Time Application Build"
	@echo "========================================"
	@echo "Targets:"
	@echo "  all      - Build the speedometer application (default)"
	@echo "  clean    - Remove build artifacts and logs"
	@echo "  install  - Install to /usr/local/bin (requires sudo)"
	@echo "  help     - Show this help message"
	@echo ""
	@echo "Usage:"
	@echo "  make              # Build application"
	@echo "  make clean        # Clean build files"
	@echo "  sudo make install # Install system-wide"
