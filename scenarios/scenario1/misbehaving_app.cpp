#include <iostream>
#include <thread>
#include <vector>
#include <atomic>
#include <csignal>
#include <cstring>
#include <random>
#include <chrono>
#include <unistd.h>
#include <fcntl.h>
#include <sched.h>
#include <sys/resource.h>

// Configuration
std::atomic<bool> g_running(true);

void signal_handler(int signum) {
    std::cout << "\nMisbehaving app terminated by signal " << signum << std::endl;
    g_running = false;
}

// Aggressive CPU consumption
void cpu_attack_thread(int id) {
    volatile uint64_t counter = 0;
    while (g_running) {
        // Tight busy loop - no sleep, no yield
        counter++;
        if (counter % 1000000000ULL == 0) {
            std::cout << "[CPU Attack " << id << "] Still consuming CPU..." << std::endl;
        }
    }
}

// Aggressive memory allocation
void memory_attack_thread() {
    std::vector<std::vector<uint8_t>*> allocations;
    const size_t ALLOC_SIZE = 100 * 1024 * 1024; // 100MB chunks
    
    uint64_t total_allocated = 0;
    
    while (g_running) {
        try {
            auto* chunk = new std::vector<uint8_t>(ALLOC_SIZE);
            
            // Touch all pages to force allocation
            for (size_t i = 0; i < chunk->size(); i += 4096) {
                (*chunk)[i] = 0xFF;
            }
            
            allocations.push_back(chunk);
            total_allocated += ALLOC_SIZE;
            
            std::cout << "[Memory Attack] Allocated " << (total_allocated / (1024*1024)) 
                      << " MB total" << std::endl;
            
            // Brief pause to avoid immediate OOM
            std::this_thread::sleep_for(std::chrono::milliseconds(100));
            
        } catch (const std::bad_alloc& e) {
            std::cout << "[Memory Attack] Allocation failed (OOM) - holding current allocations" << std::endl;
            // Hold what we have and wait
            std::this_thread::sleep_for(std::chrono::seconds(1));
        }
    }
    
    // Cleanup
    for (auto* chunk : allocations) {
        delete chunk;
    }
}

// Aggressive I/O
void io_attack_thread() {
    std::random_device rd;
    std::mt19937 gen(rd());
    std::uniform_int_distribution<uint8_t> byte_dist(0, 255);
    
    const size_t BUFFER_SIZE = 10 * 1024 * 1024; // 10MB buffer
    std::vector<uint8_t> buffer(BUFFER_SIZE);
    
    for (size_t i = 0; i < BUFFER_SIZE; ++i) {
        buffer[i] = byte_dist(gen);
    }
    
    uint64_t iteration = 0;
    
    while (g_running) {
        std::string filename = "misbehaving_io_" + std::to_string(iteration % 10) + ".tmp";
        
        int fd = open(filename.c_str(), O_WRONLY | O_CREAT | O_TRUNC, 0644);
        if (fd >= 0) {
            // Burst write with frequent syncs
            for (int i = 0; i < 10 && g_running; ++i) {
                write(fd, buffer.data(), BUFFER_SIZE);
                fsync(fd);
            }
            close(fd);
        }
        
        iteration++;
        if (iteration % 10 == 0) {
            std::cout << "[I/O Attack] " << iteration << " burst cycles completed" << std::endl;
        }
        
        // Very short pause - aggressive I/O
        std::this_thread::sleep_for(std::chrono::milliseconds(10));
    }
    
    // Cleanup files
    for (int i = 0; i < 10; ++i) {
        std::string filename = "misbehaving_io_" + std::to_string(i) + ".tmp";
        unlink(filename.c_str());
    }
}

// Attempt to escalate priority (will fail without privileges)
void attempt_priority_escalation() {
    std::cout << "[Priority Attack] Attempting to set real-time priority..." << std::endl;
    
    struct sched_param param;
    param.sched_priority = 99; // Maximum RT priority
    
    if (sched_setscheduler(0, SCHED_FIFO, &param) == 0) {
        std::cout << "[Priority Attack] SUCCESS: Acquired SCHED_FIFO priority 99!" << std::endl;
    } else {
        std::cout << "[Priority Attack] FAILED: " << strerror(errno) << " (as expected)" << std::endl;
    }
    
    // Try to set nice value
    if (setpriority(PRIO_PROCESS, 0, -20) == 0) {
        std::cout << "[Priority Attack] SUCCESS: Set nice to -20!" << std::endl;
    } else {
        std::cout << "[Priority Attack] FAILED: " << strerror(errno) << " (as expected)" << std::endl;
    }
}

int main(int argc, char* argv[]) {
    std::cout << "======================================" << std::endl;
    std::cout << "Misbehaving Application (Low-Criticality)" << std::endl;
    std::cout << "======================================" << std::endl;
    std::cout << "This application intentionally attempts to" << std::endl;
    std::cout << "disrupt system operation by aggressively" << std::endl;
    std::cout << "consuming resources." << std::endl;
    std::cout << "======================================" << std::endl;
    
    bool enable_cpu = true;
    bool enable_memory = true;
    bool enable_io = true;
    
    for (int i = 1; i < argc; ++i) {
        std::string arg = argv[i];
        if (arg == "--cpu-only") {
            enable_memory = false;
            enable_io = false;
        } else if (arg == "--memory-only") {
            enable_cpu = false;
            enable_io = false;
        } else if (arg == "--io-only") {
            enable_cpu = false;
            enable_memory = false;
        } else if (arg == "-h" || arg == "--help") {
            std::cout << "\nUsage: " << argv[0] << " [options]\n"
                      << "Options:\n"
                      << "  --cpu-only     Only CPU attack\n"
                      << "  --memory-only  Only memory attack\n"
                      << "  --io-only      Only I/O attack\n"
                      << "  -h, --help     Show this help message\n"
                      << "\nBy default, all attack types are enabled.\n"
                      << std::endl;
            return 0;
        }
    }
    
    signal(SIGINT, signal_handler);
    signal(SIGTERM, signal_handler);
    
    // Attempt privilege escalation
    attempt_priority_escalation();
    
    std::vector<std::thread> threads;
    
    // Launch attack threads
    if (enable_cpu) {
        int num_cpu_threads = std::thread::hardware_concurrency();
        if (num_cpu_threads == 0) num_cpu_threads = 4;
        
        std::cout << "\nLaunching " << num_cpu_threads << " CPU attack threads..." << std::endl;
        for (int i = 0; i < num_cpu_threads; ++i) {
            threads.emplace_back(cpu_attack_thread, i);
        }
    }
    
    if (enable_memory) {
        std::cout << "Launching memory attack thread..." << std::endl;
        threads.emplace_back(memory_attack_thread);
    }
    
    if (enable_io) {
        std::cout << "Launching I/O attack thread..." << std::endl;
        threads.emplace_back(io_attack_thread);
    }
    
    std::cout << "\n======================================" << std::endl;
    std::cout << "Misbehaving app running." << std::endl;
    std::cout << "System should contain this process and" << std::endl;
    std::cout << "prevent interference with safety-critical" << std::endl;
    std::cout << "applications." << std::endl;
    std::cout << "======================================\n" << std::endl;
    
    // Wait for all threads
    for (auto& t : threads) {
        t.join();
    }
    
    std::cout << "Misbehaving app shutdown complete." << std::endl;
    return 0;
}
