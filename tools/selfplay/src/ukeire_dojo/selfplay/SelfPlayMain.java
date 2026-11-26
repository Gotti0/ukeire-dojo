package ukeire_dojo.selfplay;

import com.epsilon.ai.decision.EpsilonUtilityProfile;
import com.epsilon.config.settings.InferenceBatchingSettings;
import com.epsilon.config.settings.SettingsLoader;
import com.epsilon.engine.GameRecorder;
import com.epsilon.prism.collect.CollectionDriver;
import com.epsilon.prism.config.settings.EpsilonSettings;
import com.epsilon.prism.eval.Arena;
import com.epsilon.prism.policy.PolicyService;
import com.epsilon.prism.run.NanoTable;
import com.epsilon.prism.run.PrismConfig;
import com.epsilon.spi.PolicyExecutionContext;
import com.epsilon.workflow.CommandLine;
import com.epsilon.workflow.CommandWorkflow;
import com.epsilon.workflow.StartupSettings;
import com.epsilon.workflow.WorkflowArguments;
import com.epsilon.workflow.WorkflowDefinition;
import com.epsilon.workflow.WorkflowKind;
import com.epsilon.workflow.WorkflowOptions;
import com.epsilon.workflow.WorkflowRegistry;
import java.io.IOException;
import java.io.UncheckedIOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardCopyOption;
import java.util.List;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;

/**
 * Prism 4석 자기대국을 반장마다 mjai 패보 파일로 쓴다. 릴리스 jar의 evaluate와 같은 설정 파일([epsilon.prism])과 같은 대국 경로(Arena)를
 * 쓰고, Arena의 관찰자 훅으로 기록기만 끼운다.
 */
public final class SelfPlayMain {
  private SelfPlayMain() {}

  public static void main(String[] arguments) throws IOException {
    StartupSettings startup =
        StartupSettings.read("epsilon-prism", EpsilonSettings.DEFAULT_RESOURCE, arguments);
    SettingsLoader settings = EpsilonSettings.load(startup.path(), startup.overrides());
    System.exit(
        CommandLine.run(
            startup.arguments(), WorkflowRegistry.of(List.of(new SelfPlayWorkflow())), settings));
  }

  record Result(int games, Path out, double seconds) {}

  static final class SelfPlayWorkflow implements CommandWorkflow<Result> {
    @Override
    public WorkflowDefinition definition() {
      return WorkflowDefinition.range(
          "selfplay-mjai",
          WorkflowKind.EVALUATION,
          "selfplay-mjai --snapshot DIR --out DIR [--games N] [--first-game ID]",
          4,
          8);
    }

    @Override
    public Result execute(WorkflowArguments arguments) throws Exception {
      WorkflowOptions options = arguments.namedOptions();
      String snapshot = options.optionalText("snapshot", null);
      String outText = options.optionalText("out", null);
      int games = Integer.parseInt(options.optionalText("games", "1"));
      // 패산은 seed와 반장 ID로만 정해진다. 실행마다 다른 패산을 얻으려면 first-game을 바꾼다.
      long firstGame = Long.parseLong(options.optionalText("first-game", "1"));
      options.requireEmpty();
      if (snapshot == null || outText == null)
        throw new IllegalArgumentException("selfplay-mjai requires --snapshot and --out");
      Path out = Files.createDirectories(Path.of(outText));
      String label = NanoTable.label(snapshot);
      String[] names = {label, label, label, label};

      PrismConfig config = PrismConfig.fromSettings(arguments.settings());
      config.validate();
      long started = System.nanoTime();
      Map<Long, MjaiRecorder> recorders = new ConcurrentHashMap<>();
      CollectionDriver.GameObserver observer =
          new CollectionDriver.GameObserver() {
            @Override
            public GameRecorder recorder(long gameId) {
              MjaiRecorder recorder = new MjaiRecorder(names);
              recorders.put(gameId, recorder);
              return recorder;
            }

            @Override
            public void gameEnded(long gameId, int[] finalScores) {
              MjaiRecorder recorder = recorders.remove(gameId);
              if (recorder == null) throw new IllegalStateException("No recorder for " + gameId);
              write(out.resolve(gameId + ".mjson"), recorder.finish(finalScores));
            }
          };
      try (var execution =
              new PolicyExecutionContext(config.encoders(), config.slots(), config.readyBatches());
          var driver =
              new CollectionDriver(
                  new CollectionDriver.Settings(
                      config.maxActiveGames(),
                      config.advanceWorkers(),
                      new InferenceBatchingSettings(config.batchWaitMicros())));
          PolicyService prism = NanoTable.open(snapshot, config, execution, NanoTable.NANO_DEVICE)) {
        Arena.detailed(
            driver,
            prism,
            prism,
            label,
            games,
            config.seed() ^ Arena.EVALUATION_SALT,
            firstGame,
            EpsilonUtilityProfile.valueOf(config.utility()),
            observer);
      }
      return new Result(games, out, (System.nanoTime() - started) / 1e9);
    }

    @Override
    public String summarize(Result result) {
      return String.format(
          "selfplay-mjai games=%d out=%s seconds=%.1f", result.games(), result.out(), result.seconds());
    }

    /** 임시 파일에 쓰고 이름을 바꿔, 중간에 멈춰도 반쯤 쓴 패보가 남지 않게 한다. */
    private static void write(Path file, List<String> lines) {
      try {
        Path temp = file.resolveSibling(file.getFileName() + ".tmp");
        Files.write(temp, lines, StandardCharsets.UTF_8);
        Files.move(temp, file, StandardCopyOption.REPLACE_EXISTING, StandardCopyOption.ATOMIC_MOVE);
      } catch (IOException e) {
        throw new UncheckedIOException(e);
      }
    }
  }
}
