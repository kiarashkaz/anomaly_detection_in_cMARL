import copy
import datetime
import os
import pprint
import time
import threading
import torch as th
from types import SimpleNamespace as SN
from utils.logging import Logger
from utils.timehelper import time_left, time_str
from os.path import dirname, abspath

from learners import REGISTRY as le_REGISTRY
from runners import REGISTRY as r_REGISTRY
from controllers import REGISTRY as mac_REGISTRY
from components.episode_buffer import ReplayBuffer
from components.transforms import OneHot

import learners.single_agent_dqn as singleDqn
from learners.single_agent_rdqn import RDQNAgent
from learners.trackers import Tracker
import numpy as np
import json

def run(_run, _config, _log):

    # check args sanity
    _config = args_sanity_check(_config, _log)

    args = SN(**_config)
    args.device = "cuda" if args.use_cuda else "cpu"

    # setup loggers
    logger = Logger(_log)

    _log.info("Experiment Parameters:")
    experiment_params = pprint.pformat(_config, indent=4, width=1)
    _log.info("\n\n" + experiment_params + "\n")

    # configure tensorboard logger
    # unique_token = "{}__{}".format(args.name, datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S"))

    try:
        map_name = _config["env_args"]["map_name"]
    except:
        map_name = _config["env_args"]["key"]   
    unique_token = f"{_config['name']}_seed{_config['seed']}_{map_name}_{datetime.datetime.now()}"

    args.unique_token = unique_token
    if args.use_tensorboard:
        tb_logs_direc = os.path.join(
            dirname(dirname(abspath(__file__))), "results", "tb_logs"
        )
        tb_exp_direc = os.path.join(tb_logs_direc, "{}").format(unique_token)
        logger.setup_tb(tb_exp_direc)

    # sacred is on by default
    logger.setup_sacred(_run)

    # Run and train
    run_sequential(args=args, logger=logger)

    # Clean up after finishing
    print("Exiting Main")

    print("Stopping all threads")
    for t in threading.enumerate():
        if t.name != "MainThread":
            print("Thread {} is alive! Is daemon: {}".format(t.name, t.daemon))
            t.join(timeout=1)
            print("Thread joined")

    print("Exiting script")

    # Making sure framework really exits
    # os._exit(os.EX_OK)


def evaluate_sequential(args, runner):
    if args.attack_active:
        if args.attack_type == "OA" or args.attack_type == "AA":
        #    adv_state_size = runner.env.get_obs_size()
         #   adv_action_size = runner.env.get_total_actions()
          #  advagent = singleDqn.DQNAgent(adv_state_size, adv_action_size)
            #advagent.create_model(gamma=args.gamma, exploration_proba_decay=0.001)
           # advagent.load_model()
        #elif args.attack_type == "AA":
            adv_args = copy.deepcopy(args)
            adv_obs_size = runner.env.get_obs_size()
            adv_actions_size = runner.env.get_total_actions()
            adv_args.exploration_proba_decay = args.adv_exploration_proba_decay
            adv_args.batch_size = args.adv_batch_size
            adv_args.buffer_size = args.adv_buffer_size
            adv_args.max_episode_length = args.env_args["time_limit"] if "time_limit" in args.env_args else args.adv_max_episode_length
            advagent = RDQNAgent(adv_obs_size, adv_actions_size, adv_args, lambda_init=np.zeros(args.n_agents-1))
            advagent.test_mode = args.adv_test_mode
            if advagent.test_mode:
                #advagent.load_model("Trained Models/DAA/LBF/coop_obs3_T20/[0, 0, 0, 0]/ep_15000")
                advagent.load_model(args.adv_load_adr)
        elif args.attack_type == "DAA":
            adv_args = copy.deepcopy(args)
            adv_obs_size = runner.env.get_obs_size()
            adv_actions_size = runner.env.get_total_actions()
            adv_args.exploration_proba_decay = args.adv_exploration_proba_decay
            adv_args.batch_size = args.adv_batch_size
            adv_args.buffer_size = args.adv_buffer_size
            adv_args.max_episode_length = args.env_args["time_limit"] if "time_limit" in args.env_args else args.adv_max_episode_length
            advagent = RDQNAgent(adv_obs_size, adv_actions_size, adv_args, lambda_init=args.lambda_DAA)
            advagent.test_mode = args.adv_test_mode
            if advagent.test_mode:
                advagent.load_model(args.adv_load_adr)
        adv_test_mode = advagent.test_mode
    else:
        advagent = None
        adv_test_mode = True
    ##############################################################################
    tracker_args = copy.deepcopy(args)
    n_tracker_agents = tracker_args.n_agents
    tracker_args.max_episode_length = args.env_args["time_limit"] if "time_limit" in args.env_args else args.adv_max_episode_length
    tracker_args.buffer_size = args.tracker_buffer_size
    tracker_args.batch_size = args.tracker_batch_size
    tracker_args.train = args.tracker_train
    thresholds = args.thresholds
    tracker_args.detect_TH = thresholds
    trackers = Tracker(n_tracker_agents, runner.env.get_obs_size(), tracker_args) #+1+tracker_args.n_actions
    if not tracker_args.train:
        trackers.load_trackers(args.tracker_load_adr)
    ###################################################################################
    for episode in range(args.test_nepisode):
        runner.run(advagent=advagent, tracker=trackers, test_mode=True)
        #advagent.constraint_value.append(trackers.normality_metric[0][1:] / trackers.t)
        if args.attack_active:
            if not advagent.test_mode and args.attack_type == "OA":
                advagent.update_exploration_probability()
                if runner.t_env >= advagent.batch_size:
                    advagent.train()
            elif not advagent.test_mode:
                if (episode + 1) > advagent.batch_size:
                    advagent.update_exploration_probability()
                    advagent.train()

            if not adv_test_mode and (episode + 1) % (args.test_nepisode/4) == 0:
                save_loc = "Trained Models/DAA"
                save_dir = os.path.join(save_loc,
                                        "{}".format(advagent.lambda_coef.tolist()))
                if not os.path.exists(save_dir):
                    os.mkdir(save_dir)
                save_dir = os.path.join(save_loc + "/{}".format(advagent.lambda_coef.tolist()),
                                        "ep_{}".format(episode+1))
                if not os.path.exists(save_dir):
                    os.mkdir(save_dir)
                advagent.save_model(save_loc + "/{}/ep_{}".format(advagent.lambda_coef.tolist(), episode+1))

        if tracker_args.train:
            if (episode + 1) % tracker_args.buffer_size == 0:
                trackers.train(trackers.buffer)
            if (episode + 1) % (args.test_nepisode/4) == 0:
                save_dir = os.path.join("Trained Models/Trackers",
                                   "ep_{}".format(episode+1))
                if not os.path.exists(save_dir):
                    os.mkdir(save_dir)
                trackers.save_trackers(save_dir)
        else:
            trackers.output_statistics(0, 0, reset=True)
            if adv_test_mode:
                trackers.out_dict["attacked"].append(runner.adv_active)
                trackers.out_dict["battle_won"].append(runner.episode_result)
                trackers.out_dict["ep_length"].append(runner.episode_len)
                if runner.adv_active:
                    trackers.out_dict["t_start"].append(runner.attack_start_t + 1)
                else:
                    trackers.out_dict["t_start"].append(1)

    if not tracker_args.train and adv_test_mode:
        trackers.output_results()
        trackers.out_dict["t_detect"] = trackers.out_dict["t_detect"].tolist()
        with open('data.json', 'w') as fp:
            json.dump(trackers.out_dict, fp, indent=4)
        #V = np.average(advagent.constraint_value, 0)
        #print("conssst: {}".format(V))

    if args.save_replay:
        runner.save_replay()

    runner.close_env()


def run_sequential(args, logger):

    # Init runner so we can get env info
    runner = r_REGISTRY[args.runner](args=args, logger=logger)

    # Set up schemes and groups here
    env_info = runner.get_env_info()
    args.n_agents = env_info["n_agents"]
    args.n_actions = env_info["n_actions"]
    args.state_shape = env_info["state_shape"]

    # Default/Base scheme
    scheme = {
        "state": {"vshape": env_info["state_shape"]},
        "obs": {"vshape": env_info["obs_shape"], "group": "agents"},
        "actions": {"vshape": (1,), "group": "agents", "dtype": th.long},
        "avail_actions": {
            "vshape": (env_info["n_actions"],),
            "group": "agents",
            "dtype": th.int,
        },
        "reward": {"vshape": (1,)},
        "terminated": {"vshape": (1,), "dtype": th.uint8},
    }
    groups = {"agents": args.n_agents}
    preprocess = {"actions": ("actions_onehot", [OneHot(out_dim=args.n_actions)])}

    buffer = ReplayBuffer(
        scheme,
        groups,
        args.buffer_size,
        env_info["episode_limit"] + 1,
        preprocess=preprocess,
        device="cpu" if args.buffer_cpu_only else args.device,
    )

    # Setup multiagent controller here
    mac = mac_REGISTRY[args.mac](buffer.scheme, groups, args)

    # Give runner the scheme
    runner.setup(scheme=scheme, groups=groups, preprocess=preprocess, mac=mac)

    # Learner
    learner = le_REGISTRY[args.learner](mac, buffer.scheme, logger, args)

    if args.use_cuda:
        learner.cuda()

    if args.checkpoint_path != "":

        timesteps = []
        timestep_to_load = 0

        if not os.path.isdir(args.checkpoint_path):
            logger.console_logger.info(
                "Checkpoint directiory {} doesn't exist".format(args.checkpoint_path)
            )
            return

        # Go through all files in args.checkpoint_path
        for name in os.listdir(args.checkpoint_path):
            full_name = os.path.join(args.checkpoint_path, name)
            # Check if they are dirs the names of which are numbers
            if os.path.isdir(full_name) and name.isdigit():
                timesteps.append(int(name))

        if args.load_step == 0:
            # choose the max timestep
            timestep_to_load = max(timesteps)
        else:
            # choose the timestep closest to load_step
            timestep_to_load = min(timesteps, key=lambda x: abs(x - args.load_step))

        model_path = os.path.join(args.checkpoint_path, str(timestep_to_load))

        logger.console_logger.info("Loading model from {}".format(model_path))
        learner.load_models(model_path)
        runner.t_env = timestep_to_load

        if args.evaluate or args.save_replay:
            runner.log_train_stats_t = runner.t_env
            evaluate_sequential(args, runner)
            logger.log_stat("episode", runner.t_env, runner.t_env)
            #logger.print_recent_stats()
            logger.console_logger.info("Finished Evaluation")
            return

    # start training
    episode = 0
    last_test_T = -args.test_interval - 1
    last_log_T = 0
    model_save_time = 0

    start_time = time.time()
    last_time = start_time

    logger.console_logger.info("Beginning training for {} timesteps".format(args.t_max))

    while runner.t_env <= args.t_max:

        # Run for a whole episode at a time
        episode_batch = runner.run(test_mode=False)
        buffer.insert_episode_batch(episode_batch)

        if buffer.can_sample(args.batch_size):
            episode_sample = buffer.sample(args.batch_size)

            # Truncate batch to only filled timesteps
            max_ep_t = episode_sample.max_t_filled()
            episode_sample = episode_sample[:, :max_ep_t]

            if episode_sample.device != args.device:
                episode_sample.to(args.device)

            learner.train(episode_sample, runner.t_env, episode)

        # Execute test runs once in a while
        n_test_runs = max(1, args.test_nepisode // runner.batch_size)
        if (runner.t_env - last_test_T) / args.test_interval >= 1.0:

            logger.console_logger.info(
                "t_env: {} / {}".format(runner.t_env, args.t_max)
            )
            logger.console_logger.info(
                "Estimated time left: {}. Time passed: {}".format(
                    time_left(last_time, last_test_T, runner.t_env, args.t_max),
                    time_str(time.time() - start_time),
                )
            )
            last_time = time.time()

            last_test_T = runner.t_env
            for _ in range(n_test_runs):
                runner.run(test_mode=True)

        if args.save_model and (
            runner.t_env - model_save_time >= args.save_model_interval
            or model_save_time == 0
        ):
            model_save_time = runner.t_env
            save_path = os.path.join(
                args.local_results_path, "models", args.unique_token, str(runner.t_env)
            )
            # "results/models/{}".format(unique_token)
            os.makedirs(save_path, exist_ok=True)
            logger.console_logger.info("Saving models to {}".format(save_path))

            # learner should handle saving/loading -- delegate actor save/load to mac,
            # use appropriate filenames to do critics, optimizer states
            learner.save_models(save_path)

        episode += args.batch_size_run

        if (runner.t_env - last_log_T) >= args.log_interval:
            logger.log_stat("episode", episode, runner.t_env)
            logger.print_recent_stats()
            last_log_T = runner.t_env

    runner.close_env()
    logger.console_logger.info("Finished Training")


def args_sanity_check(config, _log):

    # set CUDA flags
    # config["use_cuda"] = True # Use cuda whenever possible!
    if config["use_cuda"] and not th.cuda.is_available():
        config["use_cuda"] = False
        _log.warning(
            "CUDA flag use_cuda was switched OFF automatically because no CUDA devices are available!"
        )

    if config["test_nepisode"] < config["batch_size_run"]:
        config["test_nepisode"] = config["batch_size_run"]
    else:
        config["test_nepisode"] = (
            config["test_nepisode"] // config["batch_size_run"]
        ) * config["batch_size_run"]

    return config