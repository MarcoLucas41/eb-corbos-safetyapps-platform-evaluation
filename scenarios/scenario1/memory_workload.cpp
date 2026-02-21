#include <iostream>
#include <vector>
#include <atomic>
#include <csignal>
#include <cstring>
#include <random>
#include <chrono>
#include <thread>

// Configuration
std::atomic<bool> g_running(true);

void signal_handler(int signum) {
    std::cout << "\nShutting down memory workload..." << std::endl;
    g_running = false;
}

// Memory-intensive workload with random access patterns
void memory_intensive_workload(size_t alloc_size_mb) {
    std::random_device rd;
    std::mt19937 gen(rd());
    
    // Allocate large memory buffer
    size_t buffer_size = alloc_size_mb * 1024 * 1024 / sizeof(uint64_t);
    std::cout << "Allocating " << alloc_size_mb << "MB (" << buffer_size << " elements)..." << std::endl;
    
    std::vector<uint64_t> buffer(buffer_size);
    
    std::cout << "Memory allocated successfully" << std::endl;
    
    // Initialize with pattern
    for (size_t i = 0; i < buffer_size; ++i) {
        buffer[i] = i * 0xDEADBEEF;
    }
    
    std::uniform_int_distribution<size_t> index_dist(0, buffer_size - 1);
    uint64_t iteration = 0;
    
    while (g_running) {
        // Random access pattern to maximize cache misses
        for (int i = 0; i < 100000; ++i) {
            size_t idx1 = index_dist(gen);
            size_t idx2 = index_dist(gen);
            
            // Read-modify-write operations
            uint64_t temp = buffer[idx1];
            buffer[idx1] = buffer[idx2];
            buffer[idx2] = temp ^ 0xCAFEBABE;
            
            // Additional memory operations
            volatile uint64_t sum = buffer[idx1] + buffer[idx2];
            buffer[idx1] = sum * 31;
        }
        
        // Sequential access (memory bandwidth stress)
        volatile uint64_t checksum = 0;
        for (size_t i = 0; i < buffer_size; i += 64) {
            checksum += buffer[i];
        }
        
        // Write pattern (pollute caches)
        for (size_t i = 0; i < buffer_size; i += 128) {
            buffer[i] = checksum + i;
        }
        
        iteration++;
        if (iteration % 10 == 0) {
            std::cout << "Memory workload: " << iteration << " iterations" << std::endl;
        }
    }
}

int main(int argc, char* argv[]) {
    std::cout << "Memory-Intensive Workload" << std::endl;
    std::cout << "=========================" << std::endl;
    
    size_t alloc_size_mb = 512;  // Default 512MB
    
    for (int i = 1; i < argc; ++i) {
        std::string arg = argv[i];
        if (arg == "-m" || arg == "--memory") {
            if (i + 1 < argc) {
                alloc_size_mb = std::atoi(argv[++i]);
                if (alloc_size_mb < 100) alloc_size_mb = 100;
                if (alloc_size_mb > 8192) alloc_size_mb = 8192;
            }
        } else if (arg == "-h" || arg == "--help") {
            std::cout << "\nUsage: " << argv[0] << " [options]\n"
                      << "Options:\n"
                      << "  -m, --memory <MB>  Memory allocation size in MB (default: 512, max: 8192)\n"
                      << "  -h, --help         Show this help message\n"
                      << "\nGenerates memory interference through random access patterns\n"
                      << "and sequential operations to maximize cache pressure.\n"
                      << std::endl;
            return 0;
        }
    }
    
    signal(SIGINT, signal_handler);
    signal(SIGTERM, signal_handler);
    
    std::cout << "Target allocation: " << alloc_size_mb << "MB" << std::endl;
    
    memory_intensive_workload(alloc_size_mb);
    
    std::cout << "Memory workload terminated." << std::endl;
    return 0;
}
