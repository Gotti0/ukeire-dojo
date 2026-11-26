package ukeire_dojo.review;

import com.epsilon.agent.observe.Observation;
import com.epsilon.agent.observe.ObservationBatch;
import com.epsilon.agent.policy.Evaluation;
import com.epsilon.agent.runtime.Kernels;
import com.epsilon.agent.runtime.NetworkDevices;
import com.epsilon.agent.runtime.NetworkRuntime;
import com.epsilon.agent.runtime.Precision;
import com.epsilon.mahjong.rules.Variant;
import com.epsilon.prism.core.Greedy;
import com.epsilon.prism.core.TwinDiscard;
import com.epsilon.prism.policy.NetworkPolicy;
import com.epsilon.prism.policy.PolicyRequest;
import com.epsilon.prism.run.Snapshot;
import com.epsilon.prism.settings.BehaviourSettings;
import com.epsilon.reviewer.engine.format.ModelInfo;
import com.epsilon.reviewer.engine.format.ResultWriter;
import com.epsilon.reviewer.engine.load.LoadedRecord;
import com.epsilon.reviewer.engine.load.RecordLoader;
import com.epsilon.reviewer.engine.review.Analysis;
import com.epsilon.reviewer.engine.review.JobControl;
import com.epsilon.reviewer.engine.review.ReviewModel;
import java.io.ByteArrayInputStream;
import java.io.IOException;
import java.io.OutputStream;
import java.io.UncheckedIOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardCopyOption;
import java.time.Instant;
import java.time.temporal.ChronoUnit;
import java.util.ArrayList;
import java.util.List;
import java.util.UUID;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.ConcurrentLinkedQueue;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.zip.GZIPInputStream;
import java.util.zip.GZIPOutputStream;

/**
 * 여러 패보를 리뷰어와 같은 방식으로 분석해, 리뷰어 결과 문서(gzip JSON)를 패보마다 하나씩 쓴다.
 *
 * <p>리뷰어는 한 반장의 판단을 하나씩 1행 배치로 평가해 GPU가 놀았다. 여기서는 리뷰어의 분석 루프({@link Analysis})와 문서 작성기를
 * 그대로 쓰고, 반장마다 스레드를 하나씩 두어 동시에 돌린다. 모델 자리에는 {@link Batcher}를 끼워, 스레드들이 동시에 낸 평가 요청을
 * 모아 한 배치로 네트워크에 보낸다. 평가 결과(점수, 확률, 탐욕 수)를 만드는 방식은 PrismPolicy.evaluate와 같다.
 *
 * <p>사용: BatchReview --checkpoint DIR --out DIR [--device gpu0] [--threads 256] [--max-batch 2048] FILE...
 */
public final class BatchReview {
  private BatchReview() {}

  public static void main(String[] args) throws Exception {
    Path checkpoint = null;
    Path out = null;
    String device = "gpu0";
    int threads = 256;
    int maxBatch = 2048;
    List<Path> inputs = new ArrayList<>();
    for (int i = 0; i < args.length; i++) {
      switch (args[i]) {
        case "--checkpoint" -> checkpoint = Path.of(args[++i]);
        case "--out" -> out = Path.of(args[++i]);
        case "--device" -> device = args[++i];
        case "--threads" -> threads = Integer.parseInt(args[++i]);
        case "--max-batch" -> maxBatch = Integer.parseInt(args[++i]);
        default -> inputs.add(Path.of(args[i]));
      }
    }
    if (checkpoint == null || out == null || inputs.isEmpty())
      throw new IllegalArgumentException(
          "usage: BatchReview --checkpoint DIR --out DIR [--device gpu0] [--threads N] [--max-batch N] FILE...");
    Files.createDirectories(out);

    boolean gpu = NetworkDevices.device(device).isGpu();
    long started = System.nanoTime();
    try (var runtime = new NetworkRuntime(1, 1, 1);
        NetworkPolicy network =
            Snapshot.open(
                checkpoint,
                List.of(NetworkDevices.device(device)),
                runtime,
                maxBatch,
                gpu ? Precision.BFLOAT16 : Precision.FLOAT32,
                gpu ? Kernels.NATIVE : Kernels.EAGER)) {
      Variant variant = network.format().variant();
      var batcher = new Batcher(network, variant, BehaviourSettings.defaults().twinDiscard(), maxBatch);
      var queue = new ConcurrentLinkedQueue<>(inputs);
      var done = new AtomicInteger();
      var failed = new AtomicInteger();
      List<Thread> workers = new ArrayList<>();
      int count = Math.min(threads, inputs.size());
      for (int t = 0; t < count; t++) {
        batcher.workerStarted();
        Path target = out;
        workers.add(
            Thread.ofPlatform()
                .name("batch-review-" + t)
                .start(
                    () -> {
                      try {
                        for (Path input = queue.poll(); input != null; input = queue.poll()) {
                          try {
                            analyze(input, target, batcher);
                            int n = done.incrementAndGet();
                            if (n % 32 == 0) System.err.println("analyzed " + n + "/" + inputs.size());
                          } catch (RuntimeException | IOException failure) {
                            failed.incrementAndGet();
                            System.err.println("FAILED " + input.getFileName() + ": " + failure);
                          }
                        }
                      } finally {
                        batcher.workerFinished();
                      }
                    }));
      }
      for (Thread worker : workers) worker.join();
      double seconds = (System.nanoTime() - started) / 1e9;
      System.out.printf(
          "records=%d failed=%d decisions=%d batches=%d rows/batch=%.1f seconds=%.1f decisions/s=%.0f%n",
          done.get(),
          failed.get(),
          batcher.rows.get(),
          batcher.batches.get(),
          batcher.batches.get() == 0 ? 0.0 : (double) batcher.rows.get() / batcher.batches.get(),
          seconds,
          batcher.rows.get() / seconds);
    }
  }

  private static void analyze(Path input, Path out, ReviewModel model) throws IOException {
    byte[] bytes = Files.readAllBytes(input);
    if (bytes.length > 1 && (bytes[0] & 0xff) == 0x1f && (bytes[1] & 0xff) == 0x8b) {
      try (var gz = new GZIPInputStream(new ByteArrayInputStream(bytes))) {
        bytes = gz.readAllBytes();
      }
    }
    LoadedRecord record = RecordLoader.load(bytes);
    String name = input.getFileName().toString().replaceFirst("\\.(xml\\.gz|mjson|json|xml)$", "");
    Path file = out.resolve(name + ".result.json.gz");
    Path temp = out.resolve(name + ".result.json.gz.tmp");
    var info = new ModelInfo("epsilon-prism-v2", "prism-v760", "Epsilon-Prism v760", "v760");
    try (OutputStream stream = new GZIPOutputStream(Files.newOutputStream(temp));
        var writer =
            new ResultWriter(
                stream,
                UUID.randomUUID().toString(),
                Instant.now().truncatedTo(ChronoUnit.MILLIS).toString(),
                info,
                record.summary())) {
      Analysis.run(record, model, NEVER_CANCELLED, writer);
    }
    Files.move(temp, file, StandardCopyOption.REPLACE_EXISTING, StandardCopyOption.ATOMIC_MOVE);
  }

  private static final JobControl NEVER_CANCELLED =
      new JobControl() {
        @Override
        public boolean cancelled() {
          return false;
        }

        @Override
        public void progress(int percent) {}
      };

  /**
   * 여러 스레드의 1판단 평가 요청을 모아 한 배치로 보낸다. 스레드는 요청을 넣고 결과가 올 때까지 기다리므로, 모든 작업 스레드가 기다리는
   * 순간이 곧 더 모일 요청이 없는 순간이다. 그때(또는 상한에 닿았을 때) 보낸다. 시간 제한으로 보내는 방식은 쓰지 않는다. 기다릴수록 배치가
   * 커지는 게 아니라 그냥 늦어지기 때문이다.
   */
  static final class Batcher implements ReviewModel {
    /** PrismPolicy.DISPLAY_TEMPERATURE와 같은 값. 리뷰어 결과 문서의 temperature가 이 값이다. */
    private static final double TEMPERATURE = 0.002;

    private final NetworkPolicy network;
    private final Variant variant;
    private final TwinDiscard twinDiscard;
    private final int maxBatch;

    private ObservationBatch records;
    private List<PolicyRequest> pending = new ArrayList<>();
    private List<CompletableFuture<Void>> waiters = new ArrayList<>();
    private int liveWorkers;

    final AtomicInteger rows = new AtomicInteger();
    final AtomicInteger batches = new AtomicInteger();

    Batcher(NetworkPolicy network, Variant variant, TwinDiscard twinDiscard, int maxBatch) {
      this.network = network;
      this.variant = variant;
      this.twinDiscard = twinDiscard;
      this.maxBatch = maxBatch;
      records = new ObservationBatch(variant, 64 * maxBatch);
    }

    synchronized void workerStarted() {
      liveWorkers++;
    }

    synchronized void workerFinished() {
      liveWorkers--;
      // 끝난 스레드 몫만큼 기다릴 수가 줄었으므로, 남은 스레드가 모두 기다리고 있으면 지금 보내야 한다.
      if (!pending.isEmpty() && pending.size() >= liveWorkers) flush();
    }

    @Override
    public Evaluation evaluate(Observation observation) {
      int n = observation.legalActions();
      if (n == 1) return new Evaluation(new float[] {1}, new float[] {0}, 0);
      PolicyRequest request;
      CompletableFuture<Void> ready = new CompletableFuture<>();
      synchronized (this) {
        int index = records.add(observation);
        request =
            new PolicyRequest(
                this, 0, 0, null, null, false, 0, records, index,
                new Observation(variant).bind(records, index));
        pending.add(request);
        waiters.add(ready);
        if (pending.size() >= maxBatch || pending.size() >= liveWorkers) flush();
      }
      ready.join();
      float[] scores = request.scores;
      return new Evaluation(
          softmax(scores),
          scores,
          twinDiscard.play(request.legal(), Greedy.argmax(scores, request.prior, n)));
    }

    /** 모은 요청을 보낸다. 보내는 동안 배치를 계속 읽으므로 다음 요청은 새 배치에 담는다. */
    private void flush() {
      List<PolicyRequest> rowsToSend = pending;
      List<CompletableFuture<Void>> toWake = waiters;
      pending = new ArrayList<>();
      waiters = new ArrayList<>();
      records = new ObservationBatch(variant, 64 * maxBatch);
      rows.addAndGet(rowsToSend.size());
      batches.incrementAndGet();
      CompletableFuture<Void> sent;
      while (true) {
        var attempt = network.tryAcquireIngress(0);
        if (attempt.acquired()) {
          sent = network.submit(rowsToSend, attempt.ingress());
          break;
        }
        attempt.available().join();
      }
      sent.whenComplete(
          (ignored, failure) -> {
            for (CompletableFuture<Void> waiter : toWake) {
              if (failure == null) waiter.complete(null);
              else waiter.completeExceptionally(failure);
            }
          });
    }

    @Override
    public boolean valued() {
      return true;
    }

    @Override
    public double temperature() {
      return TEMPERATURE;
    }

    @Override
    public Variant variant() {
      return variant;
    }

    private static float[] softmax(float[] scores) {
      double max = Double.NEGATIVE_INFINITY;
      for (float score : scores) max = Math.max(max, score);
      double[] e = new double[scores.length];
      double sum = 0;
      for (int a = 0; a < scores.length; a++) sum += e[a] = Math.exp((scores[a] - max) / TEMPERATURE);
      float[] probabilities = new float[scores.length];
      for (int a = 0; a < scores.length; a++) probabilities[a] = (float) (e[a] / sum);
      return probabilities;
    }
  }
}
