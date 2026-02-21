#include <iostream>
#include <thread>
#include <vector>
#include <atomic>
#include <csignal>
#include <cmath>
#include <random>
#include <chrono>

// Configuration
std::atomic<bool> g_running(true);

void signal_handler(int signum) {
    std::cout << "\nShutting down CPU workload..." << std::endl;
    g_running = false;
}

// CPU-intensive computation (matrix multiplication + FFT-like operations)
void cpu_intensive_thread(int thread_id) {
    std::random_device rd;
    std::mt19937 gen(rd());
    std::uniform_real_distribution<> dis(-1.0, 1.0);
    
    const size_t MATRIX_SIZE = 256;
    std::vector<double> matrix_a(MATRIX_SIZE * MATRIX_SIZE);
    std::vector<double> matrix_b(MATRIX_SIZE * MATRIX_SIZE);
    std::vector<double> matrix_c(MATRIX_SIZE * MATRIX_SIZE);
    
    uint64_t iteration = 0;
    
    while (g_running) {
        // Initialize matrices with random values
        for (size_t i = 0; i < matrix_a.size(); ++i) {
            matrix_a[i] = dis(gen);
            matrix_b[i] = dis(gen);
        }
        
        // Matrix multiplication (compute-intensive)
        for (size_t i = 0; i < MATRIX_SIZE; ++i) {
            for (size_t j = 0; j < MATRIX_SIZE; ++j) {
                double sum = 0.0;
                for (size_t k = 0; k < MATRIX_SIZE; ++k) {
                    sum += matrix_a[i * MATRIX_SIZE + k] * matrix_b[k * MATRIX_SIZE + j];
                }
                matrix_c[i * MATRIX_SIZE + j] = sum;
            }
        }
        
        // FFT-like trigonometric operations
        for (size_t i = 0; i < 1000; ++i) {
            volatile double x = dis(gen);
            volatile double result = std::sin(x) * std::cos(x) + std::tan(x / 2.0);
            result = std::sqrt(std::abs(result)) + std::exp(result / 10.0);
        }
        
        iteration++;
        if (iteration % 10 == 0) {
            std::cout << "CPU Thread " << thread_id << ": " << iteration << " iterations" << std::endl;
        }
    }
}

int main(int argc, char* argv[]) {
    std::cout << "CPU-Intensive Workload" << std::endl;
    std::cout << "======================" << std::endl;
    
    int num_threads = 1;
    
    for (int i = 1; i < argc; ++i) {
        std::string arg = argv[i];
        if (arg == "-t" || arg == "--threads") {
            if (i + 1 < argc) {
                num_threads = std::atoi(argv[++i]);
                if (num_threads < 1) num_threads = 1;
                if (num_threads > 16) num_threads = 16;
            }
        } else if (arg == "-h" || arg == "--help") {
            std::cout << "\nUsage: " << argv[0] << " [options]\n"
                      << "Options:\n"
                      << "  -t, --threads <num>  Number of CPU threads (default: 1, max: 16)\n"
                      << "  -h, --help           Show this help message\n"
                      << "\nGenerates CPU interference through matrix operations and trigonometric calculations.\n"
                      << std::endl;
            return 0;
        }
    }
    
    signal(SIGINT, signal_handler);
    signal(SIGTERM, signal_handler);
    
    std::cout << "Starting " << num_threads << " CPU-intensive thread(s)..." << std::endl;
    
    // Launch worker threads
    std::vector<std::thread> threads;
    for (int i = 0; i < num_threads; ++i) {
        threads.emplace_back(cpu_intensive_thread, i);
    }
    
    // Wait for all threads
    for (auto& t : threads) {
        t.join();
    }
    
    std::cout << "CPU workload terminated." << std::endl;
    return 0;
}
