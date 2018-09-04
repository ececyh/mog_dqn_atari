import distdeepq
from baselines import logger


def exp(env_name='Assault',  # default setting on server
        lr=2.5e-4,
        eps=0.0003125,
        max_timesteps=25e6,
        buffer_size=1e6,
        batch_size=32,
        exp_t1=1e6,
        exp_p1=0.1,
        exp_t2=25e6,
        exp_p2=0.01,
        train_freq=4,
        learning_starts=5e4,
        target_network_update_freq=1e4,
        gamma=0.99,
        num_cpu=50,
        nb_atoms=5,
        convs=[(32, 8, 4), (64, 4, 2), (64, 3, 1)],
        hiddens=[512],
        ):

    env, _ = distdeepq.make_env(env_name)

    # logging directory setting:
    # logger.configure(dir=os.path.join('.', datetime.datetime.now().strftime("openai-%Y-%m-%d-%H-%M-%S-%f"))) 
    # logging configuration: baselines/baselines/logger.py
    logger.configure()

    model = distdeepq.models.cnn_to_dist_mlp(
        convs=convs, #[(32, 8, 4), (64, 4, 2), (64, 3, 1)],
        hiddens=hiddens, #[512], #512
        dueling=False
    )
    act = distdeepq.learn(
        env,
        p_dist_func=model,
        lr=lr,  # 1e-4
        eps=eps,
        max_timesteps=int(max_timesteps), # 25M
        buffer_size=int(buffer_size), # 1M
        batch_size=int(batch_size),
        exp_t1=exp_t1,
        exp_p1=exp_p1,
        exp_t2=exp_t2,
        exp_p2=exp_p2,
        train_freq=train_freq,
        learning_starts=learning_starts, # 50000
        target_network_update_freq=target_network_update_freq, # 10000
        gamma=gamma,
        num_cpu=num_cpu,
        prioritized_replay=False,
        dist_params={'nb_atoms': nb_atoms}
    )
    act.save("assault_model.pkl")
    env.close()


if __name__ == '__main__':
    exp(lr=2.5e-4, max_timesteps=2.5e6, buffer_size=1e4, exp_t1=1e6, exp_t2=2.5e6,
        exp_p1=0.1, exp_p2=0.01, hiddens=[256],
        learning_starts=1e4, target_network_update_freq=1e3, num_cpu=40) # setting for desktop
